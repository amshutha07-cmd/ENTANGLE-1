import os
import re
import json
import uuid
import hashlib
import hmac
import stat
import logging
import secrets
import threading
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import Optional

import platform_secret

logger = logging.getLogger(__name__)

# Root directory for the entire vault system
BASE_DIR = os.environ.get("ANSX_VAULT_HOME", os.path.expanduser("~/.ansx_vault"))
IDENTITY_DIR = os.path.join(BASE_DIR, "identities")
CONTACTS_DIR = os.path.join(BASE_DIR, "contacts")

# ─── Constants ──────────────────────────────────────────────────────────────
_RSA_KEY_SIZE   = 4096
_RSA_PUBLIC_EXP = 65537
_PBKDF2_ITER    = 600_000   # NIST recommended minimum for SHA-256
_IDENTITY_VERSION = 2
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


class IdentityError(ValueError):
    pass


def safe_name(name: str) -> str:
    """Operator / contact names become file names, so they must be plain tokens (no path parts)."""
    if not isinstance(name, str) or not _NAME_RE.match(name) or ".." in name:
        raise IdentityError(f"Invalid name {name!r}: use 1-32 letters, digits, '_', '-' or '.'.")
    return name


def public_key_fingerprint(public_pem: str) -> str:
    """SHA-256 of the DER SubjectPublicKeyInfo, as 8 groups of 8 hex chars (compare out of band)."""
    key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    h = hashlib.sha256(der).hexdigest().upper()
    return " ".join(h[i:i + 8] for i in range(0, 64, 8))


def _atomic_write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class SecurityCore:
    # In-memory only: decrypted private keys of operators who have passed NFC login this session.
    _unlocked: dict[str, dict] = {}
    _unlock_lock = threading.Lock()

    @staticmethod
    def initialize_system() -> None:
        """Creates the vault directory tree with strict 700 permissions."""
        os.makedirs(IDENTITY_DIR, mode=0o700, exist_ok=True)
        # Harden existing dirs in case they were created with loose perms
        for d in (BASE_DIR, IDENTITY_DIR):
            os.chmod(d, stat.S_IRWXU)

    @staticmethod
    def get_machine_id() -> str:
        """Informational only (MAC address; trivially spoofable). Not used for any security decision."""
        return str(uuid.getnode())

    @staticmethod
    def get_geolocation() -> str:
        """
        Informational only. IP geolocation over plain HTTP is spoofable (VPN / MITM) and
        flaky, so it is NOT part of any key derivation or access decision.
        """
        try:
            import urllib.request
            req = urllib.request.Request("http://ip-api.com/json/", headers={'User-Agent': 'Mozilla'})
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                if data.get("status") == "success":
                    return f"{round(float(data.get('lat', 0.0)), 1)},{round(float(data.get('lon', 0.0)), 1)}"
        except Exception as e:
            logger.warning("Failed to fetch geolocation: %s", e)
        return "unknown"

    # ─── key derivation ─────────────────────────────────────────────────────

    @staticmethod
    def _derive_anchor(nfc_seed: str, salt: bytes) -> bytes:
        """
        PBKDF2-HMAC-SHA256(nfc_seed, salt || platform_secret). Needs BOTH the NFC card
        and this machine's protected secret (see platform_secret.backend_name()).
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32,
            salt=salt + platform_secret.get_platform_secret(), iterations=_PBKDF2_ITER,
        )
        return kdf.derive(nfc_seed.encode())

    @staticmethod
    def _subkey(anchor: bytes, label: bytes) -> bytes:
        return hmac.new(anchor, label, hashlib.sha256).digest()

    @classmethod
    def _legacy_anchor(cls, nfc_seed: str, geolocation: str) -> str:
        """v1 identities only (MAC + IP geolocation salt). Used solely to migrate them."""
        salt = f"{cls.get_machine_id()}::{geolocation}".encode()
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITER)
        return kdf.derive(nfc_seed.encode()).hex()

    @staticmethod
    def _wrap(anchor_wrap_key: bytes, operator: str, vault_id: str, secrets_obj: dict) -> dict:
        nonce = os.urandom(12)
        ct = AESGCM(anchor_wrap_key).encrypt(
            nonce, json.dumps(secrets_obj).encode(), f"{operator}|{vault_id}".encode())
        return {"nonce": nonce.hex(), "ct": ct.hex()}

    @staticmethod
    def _unwrap(anchor_wrap_key: bytes, operator: str, vault_id: str, wrapped: dict) -> dict:
        pt = AESGCM(anchor_wrap_key).decrypt(
            bytes.fromhex(wrapped["nonce"]), bytes.fromhex(wrapped["ct"]), f"{operator}|{vault_id}".encode())
        return json.loads(pt)

    @classmethod
    def list_registered_users(cls) -> list[str]:
        """Returns all hardware-bound operator names on this machine."""
        cls.initialize_system()
        return [
            f.removesuffix(".json")
            for f in os.listdir(IDENTITY_DIR)
            if f.endswith(".json")
        ]

    # ─── registration ───────────────────────────────────────────────────────

    @classmethod
    def establish_identity(cls, operator_name: str, nfc_seed: str, auth: str = "nfc") -> str:
        """
        Binds the operator's NFC tag + this machine's protected secret into a PBKDF2 anchor,
        generates an RSA-4096 identity and an Ethereum wallet, and stores the PRIVATE keys
        only in AES-GCM-wrapped form (key derived from the anchor). Returns the vault ID.
        """
        safe_name(operator_name)
        if auth not in ("nfc", "passphrase"):
            raise IdentityError("Unknown authentication method.")
        if len(nfc_seed) < 12:
            raise IdentityError("The secret is too short (need at least 12 characters).")
        cls.initialize_system()
        filepath = os.path.join(IDENTITY_DIR, f"{operator_name}.json")
        if os.path.exists(filepath):
            raise IdentityError(f"Operator '{operator_name}' already exists on this device.")

        salt = os.urandom(32)
        anchor = cls._derive_anchor(nfc_seed, salt)

        private_key = rsa.generate_private_key(public_exponent=_RSA_PUBLIC_EXP, key_size=_RSA_KEY_SIZE)
        pem_private = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        pem_public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        vault_id = f"ANSX-{os.urandom(6).hex().upper()}"

        try:
            from eth_account import Account as EthAccount
            eth_acct = EthAccount.create()
            eth_address, eth_private_key = eth_acct.address, eth_acct.key.hex()
            logger.info("[Security] ETH wallet generated: %s", eth_address)
        except Exception as e:
            logger.warning("[Security] eth_account unavailable, skipping ETH wallet: %s", e)
            eth_address, eth_private_key = "", ""

        secret_blob = {"private_key": pem_private, "eth_private_key": eth_private_key}
        identity_data = {
            "version":         _IDENTITY_VERSION,
            "operator":        operator_name,
            "auth":            auth,
            "vault_id":        vault_id,
            "public_key":      pem_public,
            "eth_address":     eth_address,
            "machine_id":      cls.get_machine_id(),
            "secret_backend":  platform_secret.backend_name(),
            "kdf":             {"alg": "PBKDF2-SHA256", "iterations": _PBKDF2_ITER, "salt": salt.hex()},
            "verifier":        cls._subkey(anchor, b"ANSX verify").hex(),
            "wrapped":         cls._wrap(cls._subkey(anchor, b"ANSX wrap"), operator_name, vault_id, secret_blob),
        }
        _atomic_write_json(filepath, identity_data)
        with cls._unlock_lock:
            cls._unlocked[operator_name] = secret_blob

        cls._publish_identity(operator_name, pem_public, eth_address, eth_private_key, pem_private)
        logger.info("Identity established for operator '%s' (vault_id=%s)", operator_name, vault_id)
        return vault_id

    @classmethod
    def _publish_identity(cls, operator_name: str, pem_public: str, eth_address: str, eth_private_key: str,
                          pem_private: str = "") -> None:
        """Best-effort publication to the relay and the on-chain registry. Never raises."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        try:
            import web3_bridge
            web3_bridge.get_web3_engine().register_identity(operator_name, pem_public, pem_private, local_ip)
        except Exception as e:
            logger.error("[Security] Mesh registration failed: %s", e)

        if not eth_address:
            return
        try:
            from session_broker import get_session_broker
            from web3 import Web3
            broker = get_session_broker()
            if not (broker.is_connected and broker._registry):
                logger.info("[Security] On-chain registry not configured; skipping (the relay is the directory).")
                return
            account = Web3().eth.account.from_key(eth_private_key)
            balance = broker._w3.eth.get_balance(account.address)
            gas_price = broker._w3.eth.gas_price
            if balance < 500_000 * gas_price:
                logger.warning(
                    "[Security] Wallet %s has no test MATIC, so on-chain registration was NOT sent. "
                    "Fund it at %s and use the Register-on-chain action.",
                    account.address, broker.get_faucet_url(operator_name))
                return
            tx = broker._registry.functions.registerProfile(
                operator_name, pem_public, local_ip, Web3.to_checksum_address(eth_address),
            ).build_transaction({
                "chainId": 80002, "from": account.address,
                "nonce": broker._w3.eth.get_transaction_count(account.address),
                "gas": 500_000, "gasPrice": gas_price,
            })
            signed = broker._w3.eth.account.sign_transaction(tx, eth_private_key)
            tx_hash = broker._w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info("[Security] On-chain registration tx: %s (Amoy)", tx_hash.hex())
        except Exception as e:
            logger.warning("[Security] On-chain registration skipped: %s", e)

    # ─── login / unlock ─────────────────────────────────────────────────────

    @classmethod
    def _read_identity_file(cls, operator_name: str) -> Optional[dict]:
        try:
            safe_name(operator_name)
        except IdentityError:
            return None
        filepath = os.path.join(IDENTITY_DIR, f"{operator_name}.json")
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r") as f:
            return json.load(f)

    @classmethod
    def load_identity_for_user(cls, operator_name: str) -> Optional[dict]:
        """
        Identity block for a logged-in operator. Private keys ("private_key",
        "eth_private_key") are present ONLY after a successful verify_login() this session.
        """
        data = cls._read_identity_file(operator_name)
        if not data:
            return None
        public_view = {k: v for k, v in data.items() if k not in ("wrapped", "verifier", "kdf", "private_key", "eth_private_key", "hardware_anchor")}
        with cls._unlock_lock:
            unlocked = cls._unlocked.get(operator_name)
        if unlocked:
            public_view.update(unlocked)
        return public_view

    @classmethod
    def auth_mode(cls, operator_name: str) -> str:
        """"nfc" (card) or "passphrase". Legacy identities are card-based."""
        data = cls._read_identity_file(operator_name)
        return (data or {}).get("auth", "nfc")

    @classmethod
    def is_unlocked(cls, operator_name: str) -> bool:
        with cls._unlock_lock:
            return operator_name in cls._unlocked

    @classmethod
    def lock(cls, operator_name: Optional[str] = None) -> None:
        """Forget decrypted private keys (all operators if no name given)."""
        with cls._unlock_lock:
            if operator_name is None:
                cls._unlocked.clear()
            else:
                cls._unlocked.pop(operator_name, None)

    @classmethod
    def delete_identity(cls, operator_name: str) -> bool:
        """Deletes the identity block for the operator, wiping them from the device."""
        try:
            safe_name(operator_name)
        except IdentityError:
            return False
        filepath = os.path.join(IDENTITY_DIR, f"{operator_name}.json")
        cls.lock(operator_name)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info("Identity '%s' wiped from device.", operator_name)
                return True
        except Exception as e:
            logger.error("Failed to delete identity '%s': %s", operator_name, e)
        return False

    @classmethod
    def verify_login(cls, operator_name: str, nfc_seed: str) -> bool:
        """
        Verifies the NFC seed AND unwraps the operator's private keys into memory.
        A wrong card cannot derive the wrap key, so this is a cryptographic gate,
        not a UI check. Legacy (v1, plaintext-key) identities are migrated on first success.
        """
        data = cls._read_identity_file(operator_name)
        if not data or not nfc_seed:
            return False

        if data.get("version") == _IDENTITY_VERSION:
            try:
                anchor = cls._derive_anchor(nfc_seed, bytes.fromhex(data["kdf"]["salt"]))
            except Exception as e:
                logger.error("[Security] Anchor derivation failed: %s", e)
                return False
            if not hmac.compare_digest(cls._subkey(anchor, b"ANSX verify").hex(), data["verifier"]):
                return False
            try:
                secret_blob = cls._unwrap(cls._subkey(anchor, b"ANSX wrap"), operator_name, data["vault_id"], data["wrapped"])
            except Exception:
                logger.error("[Security] Identity file for '%s' failed authentication.", operator_name)
                return False
            with cls._unlock_lock:
                cls._unlocked[operator_name] = secret_blob
            return True

        return cls._migrate_legacy(operator_name, data, nfc_seed)

    @classmethod
    def _migrate_legacy(cls, operator_name: str, data: dict, nfc_seed: str) -> bool:
        stored = data.get("hardware_anchor", "")
        if not stored or "private_key" not in data:
            return False
        if not hmac.compare_digest(cls._legacy_anchor(nfc_seed, cls.get_geolocation()), stored):
            # Older builds fell back to "0.0,0.0" when the geolocation lookup failed.
            if not hmac.compare_digest(cls._legacy_anchor(nfc_seed, "0.0,0.0"), stored):
                return False
        logger.warning("[Security] Migrating legacy identity '%s': encrypting private keys at rest.", operator_name)
        salt = os.urandom(32)
        anchor = cls._derive_anchor(nfc_seed, salt)
        secret_blob = {"private_key": data["private_key"], "eth_private_key": data.get("eth_private_key", "")}
        vault_id = data["vault_id"]
        upgraded = {
            "version": _IDENTITY_VERSION, "operator": operator_name, "auth": "nfc", "vault_id": vault_id,
            "public_key": data["public_key"], "eth_address": data.get("eth_address", ""),
            "machine_id": data.get("machine_id", ""), "secret_backend": platform_secret.backend_name(),
            "kdf": {"alg": "PBKDF2-SHA256", "iterations": _PBKDF2_ITER, "salt": salt.hex()},
            "verifier": cls._subkey(anchor, b"ANSX verify").hex(),
            "wrapped": cls._wrap(cls._subkey(anchor, b"ANSX wrap"), operator_name, vault_id, secret_blob),
        }
        _atomic_write_json(os.path.join(IDENTITY_DIR, f"{operator_name}.json"), upgraded)
        with cls._unlock_lock:
            cls._unlocked[operator_name] = secret_blob
        return True

    # ─── CONTACT BOOK (PUBLIC KEY EXCHANGE, trust-on-first-use pinning) ─────

    @classmethod
    def export_public_identity(cls, operator_name: str, target_dir: str) -> str:
        """Exports only the public key into a .ansx_id file. No secret material."""
        data = cls.load_identity_for_user(operator_name)
        if not data:
            raise ValueError(f"Operator {operator_name} not found.")
        export_data = {"operator": data["operator"], "public_key": data["public_key"]}
        os.makedirs(target_dir, exist_ok=True)
        export_path = os.path.join(target_dir, f"{operator_name}.ansx_id")
        with open(export_path, "w") as f:
            json.dump(export_data, f, indent=4)
        return export_path

    @classmethod
    def _contact_path(cls, name: str) -> str:
        os.makedirs(CONTACTS_DIR, mode=0o700, exist_ok=True)
        return os.path.join(CONTACTS_DIR, f"{safe_name(name)}.json")

    @classmethod
    def get_contact_info(cls, name: str) -> Optional[dict]:
        try:
            path = cls._contact_path(name)
        except IdentityError:
            return None
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    @classmethod
    def import_contact(cls, ansx_id_path: str) -> str:
        """
        Explicit user import of a .ansx_id file. Counts as VERIFIED (delivered out of band),
        and replaces an existing pin only if the fingerprint is the same or the user re-imports.
        """
        if not os.path.exists(ansx_id_path):
            raise FileNotFoundError("ID file not found.")
        with open(ansx_id_path, "r") as f:
            data = json.load(f)
        if "operator" not in data or "public_key" not in data:
            raise ValueError("Invalid .ansx_id file format.")
        name = safe_name(data["operator"])
        fp = public_key_fingerprint(data["public_key"])   # also validates that it is a real public key
        _atomic_write_json(cls._contact_path(name), {
            "operator": name, "public_key": data["public_key"], "fingerprint": fp,
            "verified": True, "source": "file-import",
        })
        return name

    @classmethod
    def pin_discovered_contact(cls, name: str, public_key_pem: str, source: str) -> str:
        """
        Trust-on-first-use for keys learned from the network (relay / LAN / chain).
          new name        -> stored as unverified
          same key        -> no-op
          DIFFERENT key   -> rejected (possible impersonation / MITM); returns "mismatch"
        Returns "new" | "unchanged" | "mismatch".
        """
        safe_name(name)
        fp = public_key_fingerprint(public_key_pem)
        existing = cls.get_contact_info(name)
        if existing:
            if existing.get("fingerprint") == fp:
                return "unchanged"
            logger.error("[Trust] KEY MISMATCH for '%s': pinned %s, network offered %s (%s). Rejected.",
                         name, existing.get("fingerprint"), fp, source)
            return "mismatch"
        _atomic_write_json(cls._contact_path(name), {
            "operator": name, "public_key": public_key_pem, "fingerprint": fp,
            "verified": False, "source": source,
        })
        return "new"

    @classmethod
    def set_verified(cls, name: str) -> bool:
        """Mark a pinned contact as verified (the user compared fingerprints out of band)."""
        info = cls.get_contact_info(name)
        if not info:
            return False
        info["verified"] = True
        info["source"] = info.get("source", "") + "+fingerprint-confirmed"
        _atomic_write_json(cls._contact_path(name), info)
        return True

    @classmethod
    def remove_contact(cls, name: str) -> bool:
        try:
            path = cls._contact_path(name)
        except IdentityError:
            return False
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    @classmethod
    def list_contact_info(cls) -> list[dict]:
        os.makedirs(CONTACTS_DIR, mode=0o700, exist_ok=True)
        out = []
        for f_name in sorted(os.listdir(CONTACTS_DIR)):
            if f_name.endswith(".json"):
                try:
                    with open(os.path.join(CONTACTS_DIR, f_name)) as f:
                        out.append(json.load(f))
                except Exception:
                    continue
        return out

    @classmethod
    def get_contacts(cls) -> dict[str, str]:
        """Returns {operator_name: public_key} for all pinned contacts."""
        os.makedirs(CONTACTS_DIR, mode=0o700, exist_ok=True)
        contacts = {}
        for f_name in os.listdir(CONTACTS_DIR):
            if f_name.endswith(".json"):
                try:
                    with open(os.path.join(CONTACTS_DIR, f_name), "r") as f:
                        data = json.load(f)
                    contacts[data["operator"]] = data["public_key"]
                except Exception:
                    continue
        return contacts


class VaultLedger:
    """The operator's list of protected files (one entry per secure package)."""
    LEDGER_FILE = os.path.join(BASE_DIR, "vault_ledger.json")

    @classmethod
    def load(cls, owner: Optional[str] = None) -> list[dict]:
        """All entries, or only those belonging to `owner` (entries from older versions have no owner and stay visible)."""
        items = cls._load_all()
        if owner is None:
            return items
        return [it for it in items if it.get("owner") in (owner, None)]

    @classmethod
    def _load_all(cls) -> list[dict]:
        if not os.path.exists(cls.LEDGER_FILE):
            return []
        try:
            with open(cls.LEDGER_FILE, "r") as f:
                items = json.load(f)
        except Exception as e:
            logger.warning("Failed to load vault ledger: %s", e)
            return []
        for it in items:                                  # entries written by older versions
            it.setdefault("id", hashlib.sha1(it.get("ghost_map_path", "").encode()).hexdigest()[:12])
        return items

    @classmethod
    def save(cls, ledger: list[dict]) -> None:
        os.makedirs(os.path.dirname(cls.LEDGER_FILE), mode=0o700, exist_ok=True)
        _atomic_write_json(cls.LEDGER_FILE, ledger)

    @classmethod
    def add_entry(cls, original_filename: str, ghost_map_path: str, date_vaulted: str,
                  size: Optional[int] = None, storage: Optional[dict] = None, owner: Optional[str] = None) -> dict:
        entry = {
            "id": secrets.token_hex(6),
            "owner": owner,
            "original_filename": original_filename,
            "ghost_map_path": ghost_map_path,
            "date_vaulted": date_vaulted,
            "size": size,
            "storage": storage or {},
        }
        ledger = cls._load_all()
        ledger.insert(0, entry)
        cls.save(ledger)
        return entry

    @classmethod
    def get(cls, entry_id: str) -> Optional[dict]:
        return next((e for e in cls._load_all() if e["id"] == entry_id), None)

    @classmethod
    def remove_entry(cls, entry_id: str, delete_file: bool = True) -> bool:
        ledger = cls._load_all()
        keep = [e for e in ledger if e["id"] != entry_id]
        if len(keep) == len(ledger):
            return False
        if delete_file:
            for e in ledger:
                if e["id"] == entry_id:
                    try:
                        os.remove(e["ghost_map_path"])
                    except OSError:
                        pass
        cls.save(keep)
        return True
