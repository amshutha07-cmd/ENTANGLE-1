"""
vault_service.py — A.N.Sx Vault | the three things a person actually does, with no GUI code in sight.

  protect(file)                      encrypt + split into 12 pieces, store them, make a secure package
  rewrap_for(entry, receiver_key)    re-encrypt a package so ONLY the receiver can open it
  reconstruct(package, out_file)     rebuild the original file from a package you can open

Every package is signed by the person who made it (RSA-PSS over the manifest, the signer's name and the
recipient's key fingerprint), so the receiver can tell who really sent it and a package cannot be re-sealed
for someone else under the original sender's name. Packages from older versions have no signature and still open.

Every function reports progress through a callback `progress(label: str, percent: int)` and raises
VaultError with a message that is safe to show to a non-technical person.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import courier
import engine
import paths
from cloud_dispatcher import CloudDispatcher
from ghost_map import GhostMap, GhostMapError
from security_core import IdentityError, SecurityCore, VaultLedger, public_key_fingerprint, safe_name

logger = logging.getLogger(__name__)
Progress = Callable[[str, int], None]
_engine_lock = threading.Lock()          # the native engine wipes its output folder; one run at a time


# Without cloud storage every piece travels inside the package, which ends up about 11x the file's size
# (measured: 3 MB file -> 33 MB package). The relay accepts packages up to 128 MB, so cap the file well below that.
INLINE_PACKAGE_RATIO = 11
INLINE_FILE_LIMIT = 10 * 1024 * 1024


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str, default: str = "received_file") -> str:
    """
    The file name inside a package is chosen by the SENDER, so it is untrusted: keep only a plain base
    name (no folders, no control characters, no Windows-reserved names, bounded length).
    """
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch not in '<>:"|?*\x7f').strip(" .")
    stem = name.rsplit(".", 1)[0].lower()
    if not name or stem in _RESERVED or set(name) <= {"."}:
        return default
    if len(name) > 120:
        base, dot, ext = name.rpartition(".")
        name = (base[:100] + dot + ext[:15]) if dot and len(ext) <= 15 else name[:120]
    return name


def unique_path(directory: str, name: str) -> str:
    """directory/name, or directory/name (2) … if that already exists. Never overwrites."""
    base, ext = os.path.splitext(name)
    candidate, n = os.path.join(directory, name), 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return candidate


class VaultError(Exception):
    """A failure whose message can be shown to the user as-is."""


class Cancelled(VaultError):
    pass


@dataclass
class ProtectResult:
    entry: dict
    cloud: int = 0
    inline: int = 12
    upload_errors: dict = field(default_factory=dict)
    storage_configured: bool = False


def _noop(label: str, pct: int) -> None:
    pass


def _friendly_engine_error(exc: "engine.EngineError") -> str:
    friendly = {
        -1: "The file could not be read. Is it still there, and can you open it?",
        -4: "Could not write temporary files. Is your disk full?",
        -5: "This file is too large (the limit is 2 GB).",
        -10: "Not enough intact pieces to rebuild the file (at least 8 of 12 are needed).",
        -11: "The pieces do not match the key. They may be damaged, altered, or from a different package.",
        -12: "The rebuilt data is damaged.",
        -13: "Could not save the rebuilt file. Choose a different location.",
    }
    return friendly.get(exc.code, str(exc))


# ── sender signatures ─────────────────────────────────────────────────────────────────────────────
_SIG_ALG = "RSA-PSS-SHA256"
_SIG_DOMAIN = b"ANSX package signature v1\n"
_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)
KeyLookup = Callable[[str], Optional[tuple]]          # name -> (public_pem, "local" | "verified" | "unverified") or None


def _signed_bytes(manifest: dict, signer: str, recipient_fp: str) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "signature"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return _SIG_DOMAIN + f"{signer}\n{recipient_fp}\n{digest}".encode()


def _public_pem_of(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")


def sign_manifest(manifest: dict, identity: dict, recipient_public_pem: str) -> dict:
    """A copy of `manifest` signed by `identity` for exactly this recipient (any older signature is replaced)."""
    signer, private_pem = (identity or {}).get("operator"), (identity or {}).get("private_key")
    if not (signer and private_pem):
        raise VaultError("Your identity is locked. Unlock the app and try again.")
    recipient_fp = public_key_fingerprint(recipient_public_pem)
    key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    sig = key.sign(_signed_bytes(manifest, signer, recipient_fp), _PSS, hashes.SHA256())
    signed = {k: v for k, v in manifest.items() if k != "signature"}
    signed["signature"] = {"alg": _SIG_ALG, "signer": signer, "recipient": recipient_fp,
                           "sig": base64.b64encode(sig).decode()}
    return signed


def known_sender_key(name: str) -> Optional[tuple]:
    """(public_pem, trust) for an identity on this computer ("local") or a saved contact ("verified"/"unverified")."""
    try:
        own = SecurityCore.load_identity_for_user(name)
    except Exception:
        own = None
    if own and own.get("public_key"):
        return own["public_key"], "local"
    info = SecurityCore.get_contact_info(name)
    if info and info.get("public_key"):
        return info["public_key"], "verified" if info.get("verified") else "unverified"
    return None


def check_sender(manifest: dict, private_pem: str, lookup: KeyLookup = known_sender_key,
                 expected_sender: Optional[str] = None) -> dict:
    """
    Who signed this package? Returns {"signer", "status"} with status
      local      | signature matches an identity on this computer (yours, or someone who shares it)
      verified   | signature matches a key you verified
      unverified | signature matches a saved key you have not compared fingerprints for yet
      unknown    | signed, but you have no saved key for the signer, so it could not be checked
      unsigned   | made by an older version
    Raises VaultError, before anything is downloaded or rebuilt, when the signature is forged or altered, was made
    for a different recipient (forwarded), or names someone other than `expected_sender`.
    """
    block = manifest.get("signature")
    if not block:
        return {"signer": "", "status": "unsigned"}
    forged = VaultError("This package's sender signature is invalid. It may have been forged or altered, so it was not opened.")
    if not isinstance(block, dict) or block.get("alg") != _SIG_ALG:
        raise forged
    signer = block.get("signer", "")
    try:
        safe_name(signer)
    except IdentityError:
        raise forged from None
    if expected_sender and signer != expected_sender:
        raise VaultError(f"This package claims to come from {expected_sender}, but it was signed by {signer}. "
                         "It was not opened.")
    if block.get("recipient") != public_key_fingerprint(_public_pem_of(private_pem)):
        raise VaultError(f"This package was signed by {signer} for someone else and passed on to you. It was not opened.")
    known = lookup(signer)
    if not known:
        return {"signer": signer, "status": "unknown"}
    public_pem, trust = known
    try:
        serialization.load_pem_public_key(public_pem.encode("utf-8")).verify(
            base64.b64decode(block.get("sig", ""), validate=True),
            _signed_bytes(manifest, signer, block["recipient"]), _PSS, hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError):
        raise VaultError(f"This package's signature does not match {signer}'s saved key. It may have been forged "
                         "or altered, so it was not opened.") from None
    return {"signer": signer, "status": trust}


def protect(file_path: str, identity: dict, progress: Progress = _noop,
            dispatcher: Optional[CloudDispatcher] = None,
            cancel: Callable[[], bool] = lambda: False) -> ProtectResult:
    """Encrypt and split `file_path`, store the pieces, and create the owner's secure package."""
    public_pem = (identity or {}).get("public_key")
    if not public_pem:
        raise VaultError("Your identity is locked. Unlock the app and try again.")
    if not os.path.isfile(file_path):
        raise VaultError("That file no longer exists.")
    size = os.path.getsize(file_path)
    if size == 0:
        raise VaultError("The file is empty; there is nothing to protect.")

    dispatcher = dispatcher or CloudDispatcher()
    if not dispatcher.configured and size > INLINE_FILE_LIMIT:
        raise VaultError(
            f"This file is {size / 1048576:.0f} MB. Without cloud storage the largest file that can be protected and sent is "
            f"{INLINE_FILE_LIMIT // 1048576} MB, because every piece travels inside the package. "
            "Add a cloud storage account in Settings and try again.")
    key = os.urandom(32).hex()
    work = os.path.join(paths.tmp_dir(), "protect_" + os.urandom(6).hex())
    os.makedirs(work, mode=0o700)
    carrier = os.path.join(work, "carrier.png")
    try:
        progress("Encrypting and splitting your file…", 5)
        try:
            with _engine_lock:
                engine.shatter(file_path, key, out_dir=work)
        except engine.EngineError as exc:
            raise VaultError(_friendly_engine_error(exc)) from exc
        if cancel():
            raise Cancelled("Cancelled.")

        urls: dict = {}
        if dispatcher.configured:
            progress("Storing pieces in your cloud storage…", 25)
            per_shard = [0] * 11
            lock = threading.Lock()

            def on_progress(idx: int, pct: int) -> None:
                with lock:
                    per_shard[idx] = pct
                    total = sum(per_shard) / 11
                progress("Storing pieces in your cloud storage…", 25 + int(total * 0.55))

            threads = []
            for i in range(11):
                t = threading.Thread(
                    target=dispatcher.upload_shard,
                    args=(i, os.path.join(work, f"fragment_{i + 1}.ansx"), on_progress), daemon=True)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            urls = dict(dispatcher.uploaded_urls)
            if cancel():
                raise Cancelled("Cancelled.")

        progress("Creating your secure package…", 85)
        try:
            manifest = courier.build_manifest(file_path, key, work, urls, dict(dispatcher.refs))
            if (identity or {}).get("private_key"):
                manifest = sign_manifest(manifest, identity, public_pem)
            payload = json.dumps(manifest)
            GhostMap.make_carrier(carrier, len(payload) + 64)
            package_path = os.path.join(paths.outbox_dir(), f"package_{os.urandom(5).hex()}.png")
            GhostMap.hide_payload_in_image(payload, public_pem, carrier, package_path)   # sealed to YOU
        except (courier.CourierError, GhostMapError, OSError) as exc:
            raise VaultError(f"Could not create the secure package: {exc}") from exc

        n_inline = len(manifest["shard_payload"])
        entry = VaultLedger.add_entry(
            os.path.basename(file_path), package_path,
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            size=size, storage={"cloud": len(urls), "inline": n_inline}, owner=(identity or {}).get("operator"))
        progress("Done", 100)
        return ProtectResult(entry=entry, cloud=len(urls), inline=n_inline,
                             upload_errors=dict(dispatcher.errors), storage_configured=dispatcher.configured)
    finally:
        shutil.rmtree(work, ignore_errors=True)         # no plaintext-derived leftovers on disk


def open_package(package_path: str, private_pem: str) -> dict:
    """Decrypt a secure package with your private key and return its manifest."""
    if not private_pem:
        raise VaultError("Your identity is locked. Unlock the app first.")
    try:
        return json.loads(GhostMap.extract_payload_from_image(private_pem, package_path))
    except GhostMapError as exc:
        raise VaultError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise VaultError("That file is not a valid secure package.") from exc


def cloud_pieces(entry: dict, private_pem: str) -> list[dict]:
    """Where this file's pieces are in cloud storage ({"target", "key"} each). The list is sealed in the package."""
    manifest = open_package(entry["ghost_map_path"], private_pem)
    return [dict(ref) for _i, ref in sorted((manifest.get("cloud_refs") or {}).items(), key=lambda kv: int(kv[0]))]


def delete_cloud_pieces(entry: dict, private_pem: str, dispatcher: Optional[CloudDispatcher] = None) -> dict:
    """
    Delete a protected file's pieces from cloud storage. Returns {"total", "deleted", "problems"}. The local package is
    left alone: it is the only record of where the pieces are, so it must survive until every piece is really gone.
    """
    refs = cloud_pieces(entry, private_pem)
    if not refs:
        return {"total": 0, "deleted": 0, "problems": []}
    deleted, problems = (dispatcher or CloudDispatcher()).delete(refs)
    return {"total": len(refs), "deleted": deleted, "problems": problems}


def rewrap_for(entry: dict, identity: dict, receiver_public_pem: str,
               dispatcher: Optional[CloudDispatcher] = None, dest_dir: Optional[str] = None) -> str:
    """
    Open one of MY packages and seal a new copy for the receiver only. Fresh download links are issued
    when this machine holds the storage credentials. Returns the path of the new package.
    """
    manifest = open_package(entry["ghost_map_path"], (identity or {}).get("private_key", ""))
    manifest = courier.refresh_manifest_urls(manifest, dispatcher or CloudDispatcher())
    manifest = sign_manifest(manifest, identity, receiver_public_pem)      # signed after the links are refreshed
    payload = json.dumps(manifest)
    out_dir = dest_dir or paths.outbox_dir()
    carrier = os.path.join(out_dir, "carrier_" + os.urandom(4).hex() + ".png")
    out = os.path.join(out_dir, f"send_{os.urandom(5).hex()}.png")
    try:
        GhostMap.make_carrier(carrier, len(payload) + 64)
        GhostMap.hide_payload_in_image(payload, receiver_public_pem, carrier, out)
    except (GhostMapError, OSError) as exc:
        raise VaultError(f"Could not prepare the package for the receiver: {exc}") from exc
    finally:
        if os.path.exists(carrier):
            os.remove(carrier)
    return out


def reconstruct(package_path: str, private_pem: str, out_path: str, progress: Progress = _noop,
                dispatcher: Optional[CloudDispatcher] = None, lookup: KeyLookup = known_sender_key,
                expected_sender: Optional[str] = None) -> dict:
    """
    Rebuild the original file. Returns {"original_file", "pieces", "size", "signer", "signature"}; the sender's
    signature is checked (see check_sender) before any piece is fetched.
    """
    progress("Opening the secure package…", 10)
    manifest = open_package(package_path, private_pem)
    sender = check_sender(manifest, private_pem, lookup, expected_sender)
    manifest = courier.refresh_manifest_urls(manifest, dispatcher or CloudDispatcher())
    key = manifest.get("ephemeral_key", "")
    if not key:
        raise VaultError("This package does not contain a key.")

    work = os.path.join(paths.tmp_dir(), "restore_" + os.urandom(6).hex())
    try:
        progress("Collecting and checking the pieces…", 35)
        try:
            pieces = courier.fetch_shards(manifest, work)
        except courier.CourierError as exc:
            raise VaultError(str(exc)) from exc
        progress("Rebuilding your file…", 70)
        try:
            with _engine_lock:
                engine.unshatter(work, out_path, key)
        except engine.EngineError as exc:
            raise VaultError(_friendly_engine_error(exc)) from exc
        progress("Done", 100)
        return {"original_file": manifest.get("original_file", ""), "pieces": pieces,
                "size": os.path.getsize(out_path), "signer": sender["signer"], "signature": sender["status"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
