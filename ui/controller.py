"""
ui/controller.py — the application's brain. Widgets display state and call methods here; nothing in a
page talks to the network, the disk layout, or the crypto directly.

  * Every long operation is a `Job` (a cancellable QThread). `job.run()` can be called directly in tests.
  * State changes are announced with Qt signals so any page can subscribe.
"""
from __future__ import annotations

import logging
import os
import secrets
import string
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

import activity
import paths
import relay_config
import vault_service
from security_core import IdentityError, SecurityCore, VaultLedger, public_key_fingerprint
from relay_client import Cancelled as RelayCancelled, RelayClient, RelayError
import transfer

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]


# Every started job stays referenced here until its thread has really finished. Qt aborts the whole process if a
# QThread object is destroyed while its thread is still running (e.g. a controller dropped mid-lookup).
RUNNING: set = set()


def wait_for_all_jobs(ms: int = 20000) -> None:
    """Cancel and wait for every job still running anywhere (used at shutdown and between tests)."""
    for j in list(RUNNING):
        j.cancel()
        j.wait(ms)


class Job(QThread):
    """Runs fn(progress, cancelled) off the UI thread. Emits exactly one of succeeded / failed / cancelled."""

    progress = pyqtSignal(str, int)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, fn: Callable[[ProgressFn, Callable[[], bool]], Any], name: str = "job"):
        super().__init__()
        self._fn, self.name, self._cancel = fn, name, False

    def cancel(self) -> None:
        self._cancel = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:
        try:
            result = self._fn(lambda label, pct: self.progress.emit(label, int(pct)), lambda: self._cancel)
        except (vault_service.Cancelled, RelayCancelled):
            self.cancelled.emit()
        except (vault_service.VaultError, IdentityError) as exc:
            self.failed.emit(str(exc))
        except RelayError as exc:
            self.failed.emit(_relay_message(exc))
        except Exception as exc:                                        # never crash the UI thread
            logger.exception("Job %s crashed", self.name)
            self.failed.emit(f"Something went wrong: {exc}")
        else:
            self.succeeded.emit(result)


def _relay_message(exc: RelayError) -> str:
    if exc.status == 0:
        return "Cannot reach the relay. Check your internet connection and the relay address in Settings."
    if exc.status == 401 and "Timestamp" in exc.detail:
        return "Your computer's clock is wrong by more than 2 minutes. Fix the date and time, then try again."
    if exc.status == 404 and "not registered" in exc.detail:
        return "That person is not registered on this relay yet."
    if exc.status == 413:
        return exc.detail
    return exc.detail


def new_card_secret() -> str:
    """16 letters/digits (~95 bits) — exactly one Mifare block."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


class AppController(QObject):
    session_started = pyqtSignal(str)
    session_ended = pyqtSignal()
    inbox_changed = pyqtSignal(list)
    outbox_changed = pyqtSignal(list)
    relay_state_changed = pyqtSignal(str, str)         # state: connecting|online|offline|conflict|idle, message
    new_transfers = pyqtSignal(list)
    vault_changed = pyqtSignal()
    contacts_changed = pyqtSignal()
    activity_changed = pyqtSignal()
    toast = pyqtSignal(str, str)                       # message, kind: info|success|warning|error
    work_progress = pyqtSignal(str, str, str, int)     # job key, page doing it, label, percent (real work only)
    work_done = pyqtSignal(str)                        # job key

    def __init__(self) -> None:
        super().__init__()
        self.operator: Optional[str] = None
        self.relay: Optional[RelayClient] = None
        self._sync: Optional[transfer.SyncPoller] = None
        self.inbox: list[dict] = []
        self.outbox: list[dict] = []
        self.relay_state, self.relay_message = "idle", ""
        self._jobs: set[Job] = set()
        self._send_resume: dict[tuple, dict] = {}

    # ── job plumbing ─────────────────────────────────────────────────────────
    def run_job(self, job: Job, on_success: Optional[Callable] = None, on_fail: Optional[Callable] = None,
                on_progress: Optional[Callable] = None, on_cancel: Optional[Callable] = None) -> Job:
        """Wire callbacks (they run on the UI thread), keep the job alive, start it."""
        self._jobs.add(job)
        RUNNING.add(job)
        job.finished.connect(lambda j=job: RUNNING.discard(j))   # QThread.finished: the thread has ended
        if on_progress:
            job.progress.connect(on_progress)
        if on_success:
            job.succeeded.connect(on_success)
        if on_fail:
            job.failed.connect(on_fail)
        if on_cancel:
            job.cancelled.connect(on_cancel)
        for sig in (job.succeeded, job.failed, job.cancelled):
            sig.connect(lambda *_a, j=job: self._jobs.discard(j))
        if job.name in self.WORK_JOBS:                      # visible everywhere, not only on the page that started it
            key, page = str(id(job)), getattr(job, "page", "") or self.WORK_PAGES.get(job.name, "home")
            job.progress.connect(lambda label, pct, k=key, p=page: self.work_progress.emit(k, p, label, pct))
            for sig in (job.succeeded, job.failed, job.cancelled):
                sig.connect(lambda *_a, k=key: self.work_done.emit(k))
            self.work_progress.emit(key, page, self.WORK_START.get(job.name, "Working…"), 0)
        job.start()
        return job

    WORK_JOBS = ("protect", "send", "accept", "restore", "open-package")
    WORK_PAGES = {"protect": "vault", "restore": "vault", "send": "send", "accept": "inbox", "open-package": "inbox"}
    WORK_START = {"protect": "Protecting…", "restore": "Restoring…", "send": "Sending…", "accept": "Receiving…",
                  "open-package": "Opening a package…"}

    def busy(self) -> bool:
        """Is work someone would lose still running (protecting, sending, receiving, restoring)? Background lookups
        such as refreshing the directory do not count."""
        return any(j.isRunning() and j.name in self.WORK_JOBS for j in list(self._jobs))

    def wait_for_jobs(self, ms: int = 5000) -> None:
        for j in list(self._jobs):
            j.cancel()
            j.wait(ms)

    # ── identities ───────────────────────────────────────────────────────────
    def operators(self) -> list[str]:
        return sorted(SecurityCore.list_registered_users())

    def auth_mode(self, name: str) -> str:
        return SecurityCore.auth_mode(name)

    def reader_connected(self) -> bool:
        """Cheap and non-blocking: is a plausible reader plugged in? (The real handshake happens on use.)"""
        try:
            import nfc_serial
            return nfc_serial.reader_status() in ("connected", "found")
        except Exception:
            return False

    def make_register_job(self, name: str, auth: str, passphrase: str = "") -> Job:
        """Create an identity. For a card: write a fresh secret to the card first, then derive keys from it."""
        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> str:
            secret = passphrase
            if auth == "nfc":
                progress("Hold a blank card on the reader…", 10)
                import nfc_serial
                bridge = nfc_serial.get_shared_bridge()
                if not bridge.is_connected():
                    raise IdentityError("No card reader found. Plug it in (or choose the passphrase option).")
                # Read before writing: if this card already opens an identity here, overwriting it would lock that
                # identity out for good. A card that cannot be read cannot be written either, so this is no extra hurdle.
                try:
                    current = bridge.read_payload_from_tag(timeout=45, cancelled=cancelled)
                except nfc_serial.ReaderError as exc:
                    raise IdentityError(str(exc)) from exc
                if cancelled():
                    raise vault_service.Cancelled("Cancelled.")
                if current is None:
                    raise IdentityError("Could not read the card. Hold a Mifare Classic card flat on the reader and try again.")
                progress("Checking the card is not already in use…", 20)
                taken = SecurityCore.identities_unlocked_by(current)
                if taken:
                    who = ", ".join(f"'{t}'" for t in taken)
                    raise IdentityError(
                        f"This card already unlocks {who}. Writing a new identity to it would erase that key and lock "
                        f"{who} out for good. Use a different blank card.")
                progress("Writing your card… keep it on the reader", 30)
                secret = new_card_secret()
                try:
                    written = bridge.write_payload_to_tag(secret, timeout=45, same_card=True)   # the card just checked
                except nfc_serial.ReaderError as exc:
                    raise IdentityError(str(exc)) from exc
                if not written:
                    raise IdentityError("Could not write to the card. Keep it on the reader and try again.")
            progress("Creating your encryption keys… (a few seconds)", 45)
            SecurityCore.establish_identity(name, secret, auth=auth)
            return name
        return Job(work, "register")

    def make_login_job(self, name: str, passphrase: str = "") -> Job:
        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> str:
            secret = passphrase
            if SecurityCore.auth_mode(name) == "nfc":
                progress("Hold your card on the reader…", 10)
                import nfc_serial
                bridge = nfc_serial.get_shared_bridge()
                if not bridge.is_connected():
                    raise IdentityError("No card reader found. Plug it in and try again.")
                try:
                    secret = bridge.read_payload_from_tag(timeout=30, cancelled=cancelled) or ""
                except nfc_serial.ReaderError as exc:
                    raise IdentityError(str(exc)) from exc
                if cancelled():
                    raise vault_service.Cancelled("Cancelled.")
                if not secret:
                    raise IdentityError("No card detected. Hold it flat on the reader.")
            progress("Unlocking…", 60)
            if not SecurityCore.verify_login(name, secret):
                raise IdentityError("That card or passphrase does not unlock this identity.")
            if SecurityCore.auth_mode(name) == "nfc" and getattr(bridge, "last_key", None) == "D":
                # A card from before per-card keys: lock it now that we know it is really this person's card.
                progress("Protecting your card… keep it on the reader", 85)
                try:
                    if bridge.lock_card(secret):
                        logger.info("Card for %s is now locked with its own key.", name)
                except nfc_serial.ReaderError as exc:
                    logger.warning("Could not lock the card yet (will try at the next login): %s", exc)
            return name
        return Job(work, "login")

    def begin_session(self, name: str) -> None:
        """Call on the UI thread after a successful login/registration."""
        self.operator = name
        identity = SecurityCore.load_identity_for_user(name) or {}
        self._start_relay(name, identity)
        activity.add("security", "Unlocked", operator=name)
        self.session_started.emit(name)
        self.activity_changed.emit()

    def logout(self) -> None:
        self._stop_relay()
        self.wait_for_jobs()
        name = self.operator
        SecurityCore.lock()
        self.operator = None
        self.inbox, self.outbox = [], []
        self._set_relay_state("idle", "")
        if name:
            activity.add("security", "Locked", operator=name)
        self.session_ended.emit()

    def delete_identity(self, name: str) -> bool:
        if self.operator == name:
            self.logout()
        return SecurityCore.delete_identity(name)

    def identity(self) -> dict:
        return (SecurityCore.load_identity_for_user(self.operator) or {}) if self.operator else {}

    def my_fingerprint(self) -> str:
        pem = self.identity().get("public_key", "")
        try:
            return public_key_fingerprint(pem) if pem else ""
        except Exception:
            return ""

    # ── relay session ────────────────────────────────────────────────────────
    def _set_relay_state(self, state: str, message: str) -> None:
        self.relay_state, self.relay_message = state, message
        self.relay_state_changed.emit(state, message)

    def _start_relay(self, name: str, identity: dict) -> None:
        self._stop_relay()
        private_pem, public_pem = identity.get("private_key"), identity.get("public_key")
        if not (private_pem and public_pem):
            self._set_relay_state("offline", "Keys are locked.")
            return
        import web3_bridge
        self.relay = RelayClient(relay_config.get_relay_url(), name, private_pem)
        mesh = web3_bridge.get_web3_engine()
        self._sync = transfer.SyncPoller(self.relay, on_start=lambda: mesh.ensure_registered(name, public_pem, private_pem))
        self._sync.inbox_updated.connect(self._on_inbox)
        self._sync.outbox_updated.connect(self._on_outbox)
        self._sync.new_items.connect(self._on_new)
        self._sync.status.connect(self._on_sync_status)
        self._set_relay_state("connecting", "Connecting to the relay…")
        self._sync.start()

    def _stop_relay(self) -> None:
        if self._sync is not None:
            self._sync.stop()
            self._sync.wait(3000)
            self._sync = None
        self.relay = None

    def restart_relay(self) -> None:
        if self.operator:
            self._start_relay(self.operator, self.identity())

    def _on_sync_status(self, text: str) -> None:
        if text.startswith("●"):
            self._set_relay_state("online", "Connected")
        elif "DIFFERENT key" in text:
            self._set_relay_state("conflict", "This name is already taken on the relay by a different key.")
        else:
            self._set_relay_state("offline", text.lstrip("⚠ ").strip())

    def _on_inbox(self, items: list) -> None:
        self.inbox = items
        self.inbox_changed.emit(items)

    def _on_outbox(self, items: list) -> None:
        old = {o["id"]: o["state"] for o in self.outbox}
        self.outbox = items
        for it in items:                                    # tell the sender when something changes state
            before = old.get(it["id"])
            if before and before != it["state"]:
                if it["state"] == "delivered":
                    self.toast.emit(f"{it['to']} received your file.", "success")
                    activity.add("delivered", f"{it['to']} received your file", operator=self.operator or "")
                elif it["state"] == "rejected":
                    self.toast.emit(f"{it['to']} declined your file.", "warning")
                    activity.add("declined", f"{it['to']} declined your file", operator=self.operator or "")
                elif it["state"] == "expired":
                    self.toast.emit(f"Your file to {it['to']} expired before it was picked up.", "warning")
                self.activity_changed.emit()
        self.outbox_changed.emit(items)

    def _on_new(self, items: list) -> None:
        self.new_transfers.emit(items)

    # ── trust ────────────────────────────────────────────────────────────────
    @staticmethod
    def trust(sender: str, relay_fp: str = "") -> str:
        """verified | unverified | changed | unknown"""
        info = SecurityCore.get_contact_info(sender)
        if not info:
            return "unknown"
        if relay_fp and info.get("fingerprint") != relay_fp:
            return "changed"
        return "verified" if info.get("verified") else "unverified"

    def contacts(self) -> list[dict]:
        return [c for c in SecurityCore.list_contact_info() if c.get("operator") != self.operator]

    def verify_contact(self, name: str) -> bool:
        ok = SecurityCore.set_verified(name)
        if ok:
            activity.add("security", f"Verified {name}'s key", operator=self.operator or "")
            self.contacts_changed.emit()
            self.activity_changed.emit()
        return ok

    def import_contact(self, path: str) -> str:
        name = SecurityCore.import_contact(path)
        activity.add("security", f"Imported and verified {name}", operator=self.operator or "")
        self.contacts_changed.emit()
        return name

    def remove_contact(self, name: str) -> bool:
        ok = SecurityCore.remove_contact(name)
        if ok:
            self.contacts_changed.emit()
        return ok

    def export_identity(self, folder: str) -> str:
        return SecurityCore.export_public_identity(self.operator, folder)

    def make_directory_job(self) -> Job:
        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> list:
            import web3_bridge
            progress("Looking up people on the relay…", 30)
            return web3_bridge.get_web3_engine().sync_global_contacts(skip_username=self.operator or "")
        return Job(work, "directory")

    # ── vault ────────────────────────────────────────────────────────────────
    def vault_entries(self) -> list[dict]:
        """Only MY protected files (several people can share one computer)."""
        return VaultLedger.load(owner=self.operator)

    def make_protect_job(self, path: str) -> Job:
        identity = self.identity()

        def work(progress: ProgressFn, cancelled: Callable[[], bool]):
            return vault_service.protect(path, identity, progress=progress, cancel=cancelled)
        return Job(work, "protect")

    def after_protect(self, result: "vault_service.ProtectResult") -> None:
        e = result.entry
        activity.add("protected", f"Protected {e['original_filename']}",
                     (f"{result.cloud} of {result.cloud + result.inline} pieces in cloud storage" if result.cloud
                      else "All 12 pieces carried inside the package"),
                     operator=self.operator or "")
        self.vault_changed.emit()
        self.activity_changed.emit()

    def delete_entry(self, entry_id: str) -> bool:
        entry = VaultLedger.get(entry_id)
        ok = VaultLedger.remove_entry(entry_id)
        if ok and entry:
            activity.add("security", f"Removed {entry['original_filename']} from your vault", operator=self.operator or "")
            self.vault_changed.emit()
            self.activity_changed.emit()
        return ok

    def make_restore_job(self, entry: dict, out_path: Optional[str] = None) -> Job:
        """Rebuild one of my own files. Default destination: Downloads/A.N.Sx Vault/<original name>."""
        return self._make_rebuild_job(entry["ghost_map_path"], out_path, "restore",
                                      fallback_name=entry.get("original_filename", ""), expected_sender=self.operator)

    def make_open_package_job(self, package_path: str) -> Job:
        """Open a package file that arrived outside the relay (USB stick, chat, e-mail)."""
        return self._make_rebuild_job(package_path, None, "open-package")

    @staticmethod
    def sender_key(name: str) -> Optional[tuple]:
        """
        Key to check a package signature against: an identity here or a saved contact, else the relay's key for that
        name, saved as UNVERIFIED on first use (the same rule as sending; a later different key is refused).
        """
        known = vault_service.known_sender_key(name)
        if known:
            return known
        try:
            import web3_bridge
            return web3_bridge.get_web3_engine().resolve_trusted_public_key(name), "unverified"
        except Exception as exc:
            logger.info("No key found for signer %s: %s", name, exc)
            return None

    @staticmethod
    def signature_note(info: dict) -> tuple[str, str]:
        """(sentence, banner kind) describing who signed a package that was just opened."""
        signer, status = info.get("signer", ""), info.get("signature", "unsigned")
        if status == "verified":
            return f"Signed by {signer}, whose key you verified.", "success"
        if status == "local":
            return f"Signed by {signer}, an identity on this computer.", "success"
        if status == "unverified":
            return (f"Signed by {signer}. You haven't verified their key yet, so compare fingerprints before trusting "
                    "this file.", "warning")
        if status == "unknown":
            return (f"It says it is from {signer}, but no key for them could be found, so the signature could not be "
                    "checked.", "warning")
        return ("This package has no sender signature (it was made by an older version), so who made it cannot be "
                "confirmed.", "warning")

    def _make_rebuild_job(self, package_path: str, out_path: Optional[str], name: str, fallback_name: str = "",
                          expected_sender: Optional[str] = None) -> Job:
        private_pem = self.identity().get("private_key", "")

        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> dict:
            target = out_path or os.path.join(paths.tmp_dir(), "rebuild_" + secrets.token_hex(6))
            info = vault_service.reconstruct(package_path, private_pem, target, progress=progress,
                                             lookup=self.sender_key, expected_sender=expected_sender)
            if not out_path:
                final = vault_service.unique_path(
                    paths.downloads_dir(), vault_service.safe_filename(info.get("original_file") or fallback_name))
                os.replace(target, final)
                info["path"] = final
            else:
                info["path"] = out_path
            return info
        return Job(work, name)

    def after_restore(self, name: str) -> None:
        activity.add("restored", f"Restored {name}", operator=self.operator or "")
        self.activity_changed.emit()

    # ── sending ──────────────────────────────────────────────────────────────
    def has_pending_upload(self, entry_id: str, recipient: str) -> bool:
        return (entry_id, recipient) in self._send_resume

    def make_send_job(self, entry: dict, recipient: str) -> Job:
        """
        Wrap the package for `recipient` (their PINNED key only), then upload it. If a previous attempt for the same
        file and person was interrupted, the upload resumes from the chunks the relay already has.
        """
        identity, client = self.identity(), self.relay
        if client is None:
            raise IdentityError("Not connected to the relay.")
        key = (entry["id"], recipient)
        resume = self._send_resume.get(key)

        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> dict:
            import web3_bridge
            if resume and os.path.exists(resume["path"]):
                out_path, resume_id = resume["path"], resume["id"]
                progress(f"Resuming the upload to {recipient}…", 10)
            else:
                progress(f"Securing the package for {recipient}…", 5)
                try:
                    pub = web3_bridge.get_web3_engine().resolve_trusted_public_key(recipient)
                except ValueError as exc:
                    raise vault_service.VaultError(str(exc)) from exc
                out_path, resume_id = vault_service.rewrap_for(entry, identity, pub), None
            try:
                tid = client.send_file(
                    out_path, recipient, resume_id=resume_id, cancel=cancelled,
                    progress=lambda d, t: progress(f"Uploading to {recipient}…", 10 + int(d * 88 / max(t, 1))))
            except RelayCancelled:
                self._send_resume.pop(key, None)
                try:
                    if getattr(client, "last_transfer_id", ""):
                        client.cancel_transfer(client.last_transfer_id)
                except RelayError:
                    pass
                finally:
                    _silent_remove(out_path)
                raise
            except RelayError as exc:
                if exc.retryable and getattr(client, "last_transfer_id", ""):
                    self._send_resume[key] = {"path": out_path, "id": client.last_transfer_id}   # press Send again to resume
                else:
                    self._send_resume.pop(key, None)
                    _silent_remove(out_path)
                raise
            self._send_resume.pop(key, None)
            _silent_remove(out_path)                       # the sender cannot open it anyway; leave no copy
            return {"id": tid, "to": recipient}
        return Job(work, "send")

    # The relay only knows "a package for sam"; remember locally which file each transfer carried, so the Sent list
    # can say "Q3 board deck.pdf → sam". Kept next to the vault list (same private folder), newest 500 only.
    @staticmethod
    def _sent_names_path() -> str:
        return os.path.join(paths.vault_home(), "sent_names.json")

    def sent_file_name(self, transfer_id: str) -> str:
        import json
        try:
            with open(self._sent_names_path()) as f:
                return str(json.load(f).get(transfer_id, {}).get("file", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def remember_sent(self, transfer_id: str, file_name: str) -> None:
        import json
        from security_core import _atomic_write_json
        try:
            with open(self._sent_names_path()) as f:
                names = json.load(f)
            if not isinstance(names, dict):
                names = {}
        except (OSError, ValueError):
            names = {}
        names[transfer_id] = {"file": file_name, "owner": self.operator or ""}
        if len(names) > 500:
            names = dict(list(names.items())[-500:])
        os.makedirs(paths.vault_home(), mode=0o700, exist_ok=True)
        _atomic_write_json(self._sent_names_path(), names)

    def after_send(self, entry: dict, recipient: str, transfer_id: str = "") -> None:
        if transfer_id:
            self.remember_sent(transfer_id, entry.get("original_filename", ""))
        activity.add("sent", f"Sent {entry['original_filename']} to {recipient}", operator=self.operator or "")
        self.activity_changed.emit()

    # ── receiving ────────────────────────────────────────────────────────────
    def make_accept_job(self, item: dict, save_path: Optional[str] = None) -> Job:
        """Download, verify and open an inbox item. Default destination: Downloads/A.N.Sx Vault/<original name>."""
        client, private_pem = self.relay, self.identity().get("private_key", "")
        if client is None:
            raise IdentityError("Not connected to the relay.")

        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> dict:
            os.makedirs(paths.incoming_dir(), mode=0o700, exist_ok=True)
            package = os.path.join(paths.incoming_dir(), f"{item['id']}.png")
            client.download(item, package, cancel=cancelled,
                            progress=lambda d, t: progress(f"Downloading from {item.get('from', '?')}…", int(d * 55 / max(t, 1))))
            target = save_path or os.path.join(paths.tmp_dir(), "rebuild_" + secrets.token_hex(6))
            try:
                info = vault_service.reconstruct(
                    package, private_pem, target,
                    progress=lambda label, pct: progress(label, 55 + int(pct * 0.45)),
                    lookup=self.sender_key, expected_sender=item.get("from") or None)
            finally:
                _silent_remove(package)
            if save_path:
                info["path"] = save_path
            else:
                final = vault_service.unique_path(
                    paths.downloads_dir(), vault_service.safe_filename(info.get("original_file")))
                os.replace(target, final)
                info["path"] = final
            info["from"] = item.get("from", "")
            return info
        return Job(work, "accept")

    def after_receive(self, info: dict) -> None:
        activity.add("received", f"Received {info.get('original_file') or 'a file'} from {info.get('from', '?')}",
                     operator=self.operator or "")
        self.activity_changed.emit()

    def cancel_outgoing(self, transfer_id: str) -> None:
        if self.relay is None:
            raise IdentityError("Not connected to the relay.")
        self.relay.cancel_transfer(transfer_id)
        self.toast.emit("Transfer cancelled.", "info")

    def decline(self, item: dict) -> None:
        if self.relay is None:
            raise IdentityError("Not connected to the relay.")
        self.relay.reject(item["id"])
        activity.add("declined", f"Declined a file from {item.get('from', '?')}", operator=self.operator or "")
        self.activity_changed.emit()

    # ── settings ─────────────────────────────────────────────────────────────
    def make_relay_test_job(self, url: str) -> Job:
        def work(progress: ProgressFn, cancelled: Callable[[], bool]) -> dict:
            url_n = relay_config.normalize(url)
            probe = RelayClient(url_n, retries=0, timeout=(4, 6))
            if not probe.ping():
                raise vault_service.VaultError("Could not reach that address. Check the spelling and your connection.")
            limits = probe.limits()
            return {"url": url_n, "limits": limits, "secure": relay_config.is_secure(url_n)}
        return Job(work, "relay-test")

    def apply_relay_url(self, url: str) -> str:
        url = relay_config.set_relay_url(url)
        self.restart_relay()
        return url

    def storage_targets(self) -> list[dict]:
        import cloud_dispatcher
        return cloud_dispatcher.load_targets()

    def save_storage_targets(self, targets: list[dict]) -> None:
        import cloud_dispatcher
        cloud_dispatcher.save_targets(targets)

    def make_storage_test_job(self, target: dict) -> Job:
        def work(progress: ProgressFn, cancelled: Callable[[], bool]):
            import check_storage
            progress("Testing the connection…", 40)
            return check_storage.run_checks(target)
        return Job(work, "storage-test")


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── preferences (stored in config.json) ─────────────────────────────────────────────────────────
def pref(key: str, default=None):
    return relay_config.load_config().get(key, default)


def set_pref(key: str, value) -> None:
    relay_config.save_config(**{key: value})
