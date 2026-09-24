"""
ANSX Identity Mesh Bridge
Connects the Vault application to the ANSX Sovereign Identity Mesh relay node.
Falls back to LAN discovery + disk cache if the relay is unreachable.
"""
import os
import json
import socket
import logging
import threading
import time
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Public key registry — used ONLY for identity discovery, NOT for NFC auth.
# NFC authentication is 100% local hardware. The relay just stores public keys.
import relay_config

RELAY_URL = relay_config.get_relay_url()   # kept for backwards compatibility; use relay_config at call time

_REGISTRY_PATH = os.path.expanduser("~/.ansx_vault/network_registry.json")
_REGISTRY_LOCK = threading.Lock()
_BROADCAST_PORT = 8097
_BROADCAST_INTERVAL = 15

WEB3_ENGINE = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _relay_get(path: str) -> dict:
    url = f"{relay_config.get_relay_url()}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "ANSxVault/2.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def _relay_post(path: str, payload: dict) -> dict:
    url = f"{relay_config.get_relay_url()}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ANSxVault/2.0"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


# ─── Disk-persisted local cache ───────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if os.path.exists(_REGISTRY_PATH):
            with open(_REGISTRY_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(reg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
        tmp = _REGISTRY_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(reg, f, indent=2)
        os.replace(tmp, _REGISTRY_PATH)
    except Exception as e:
        logger.warning("Cache save failed: %s", e)


def _merge_into_cache(username: str, public_key: str, ip: str):
    with _REGISTRY_LOCK:
        reg = _load_cache()
        reg[username] = {"public_key": public_key, "ip": ip}
        _save_cache(reg)


# ─── Pin discovered peers (trust-on-first-use) ───────────────────────────────

def _save_to_contacts(username: str, public_key: str, source: str = "network") -> str:
    """
    Records a peer learned from the network as an UNVERIFIED pinned contact. A later,
    different key for the same name is rejected (see SecurityCore.pin_discovered_contact).
    Returns "new" | "unchanged" | "mismatch" | "invalid".
    """
    try:
        from security_core import SecurityCore
        return SecurityCore.pin_discovered_contact(username, public_key, source)
    except Exception as e:
        logger.warning("[Trust] Ignoring invalid peer record for %r: %s", username, e)
        return "invalid"


# ─── LAN broadcast fallback (same WiFi) ──────────────────────────────────────

class _LANDiscovery:
    def __init__(self):
        self._running = False
        self._my_entry = None

    def start_listener(self):
        self._running = True
        t = threading.Thread(target=self._listen_loop, daemon=True, name="ANSX-LAN-Listen")
        t.start()

    def announce(self, username, public_key, ip):
        self._my_entry = {"username": username, "public_key": public_key, "ip": ip}
        t = threading.Thread(target=self._broadcast_loop, daemon=True, name="ANSX-LAN-Bcast")
        t.start()

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = json.dumps(self._my_entry).encode()
        while self._running and self._my_entry:
            try:
                sock.sendto(msg, ("255.255.255.255", _BROADCAST_PORT))
            except Exception:
                pass
            time.sleep(_BROADCAST_INTERVAL)
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # cross-platform (SO_REUSEPORT fails on Windows)
            sock.bind(("", _BROADCAST_PORT))
        except Exception as e:
            logger.error("[LAN] Bind failed: %s", e)
            return
        sock.settimeout(1.0)
        logger.info("[LAN] Listening for peers on UDP port %d", _BROADCAST_PORT)
        while self._running:
            try:
                data, addr = sock.recvfrom(65535)
                entry = json.loads(data.decode())
                u, pk = entry.get("username"), entry.get("public_key")
                ip = addr[0]   # trust the packet's source address, not a self-declared one
                if (isinstance(u, str) and isinstance(pk, str) and len(u) <= 32 and len(pk) <= 4096
                        and _save_to_contacts(u, pk, "lan") in ("new", "unchanged")):
                    _merge_into_cache(u, pk, ip)
                    logger.info("[LAN] Peer seen: %s @ %s (unverified until you compare fingerprints)", u, ip)
            except socket.timeout:
                pass
            except Exception:
                pass
        sock.close()

    def stop(self):
        self._running = False


_LAN = _LANDiscovery()


# ─── Main Engine ──────────────────────────────────────────────────────────────

class ANSXMeshEngine:
    """
    Connects to the ANSX Sovereign Identity Mesh for global PKI operations.
    Falls back to LAN broadcast + disk cache when relay is unreachable.
    """

    def __init__(self):
        self._ok_val, self._ok_at, self._ok_url = False, 0.0, ""
        _LAN.start_listener()
        if self._relay_ok:
            logger.info("[Mesh] Connected to relay at %s", relay_config.get_relay_url())
        else:
            logger.warning("[Mesh] Relay unreachable, operating in LAN+cache mode.")

    @property
    def _relay_ok(self) -> bool:
        """Reachability, re-checked at most every 10 s and whenever the relay address changes."""
        url, now = relay_config.get_relay_url(), time.time()
        if url != self._ok_url or now - self._ok_at > 10:
            self._ok_val, self._ok_at, self._ok_url = self._ping_relay(), now, url
        return self._ok_val

    def _ping_relay(self) -> bool:
        try:
            _relay_get("/")
            return True
        except Exception:
            return False

    def register_identity(self, username: str, public_key: str, private_pem: str = "", ip_address: str = None):
        """
        Publish this operator's public key to the relay. The request is SELF-SIGNED with the private
        key (proof of possession), so nobody can register a key they do not hold, and a taken name
        cannot be overwritten. Always also remembered locally and announced on the LAN.
        """
        _merge_into_cache(username, public_key, ip_address or _get_local_ip())
        _LAN.announce(username, public_key, ip_address or _get_local_ip())
        if not (self._relay_ok and private_pem):
            logger.info("[Mesh] Registered locally only (relay offline or key locked).")
            return
        self.ensure_registered(username, public_key, private_pem)

    def ensure_registered(self, username: str, public_key: str, private_pem: str) -> str:
        """
        Idempotent: registers if the relay has never seen `username`, and refuses to continue if the relay
        already holds a DIFFERENT key for that name. Returns "registered" | "ok" | "conflict" | "offline".
        """
        from relay_client import RelayClient, RelayError
        from security_core import public_key_fingerprint
        client = RelayClient(relay_config.get_relay_url(), username, private_pem, retries=2)
        try:
            existing = client.resolve(username)
            if existing["fingerprint"] != public_key_fingerprint(public_key):
                logger.error("[Mesh] The relay already has a DIFFERENT key for '%s' (%s). Not overwriting.",
                             username, existing["fingerprint"][:17])
                return "conflict"
            client.heartbeat()
            return "ok"
        except RelayError as e:
            if e.status != 404:
                logger.warning("[Mesh] Relay unavailable: %s", e)
                return "offline"
        try:
            client.register(public_key)
            logger.info("[Mesh] Identity '%s' anchored on the relay.", username)
            return "registered"
        except RelayError as e:
            logger.warning("[Mesh] Registration failed: %s", e)
            return "conflict" if e.status == 409 else "offline"

    def fetch_public_key(self, username: str) -> str:
        # Try relay first
        if self._relay_ok:
            try:
                data = _relay_get(f"/v1/identity/resolve/{username}")
                # Also update our local cache with fresh data
                _merge_into_cache(username, data["public_key"], data.get("ip_address", ""))
                return data["public_key"]
            except Exception as e:
                logger.warning("[Mesh] Relay resolve failed, using cache: %s", e)

        # Fall back to local cache
        cache = _load_cache()
        entry = cache.get(username)
        if not entry:
            raise ValueError(f"Operator '{username}' not found on the ANSX Identity Mesh.")
        return entry["public_key"]

    def resolve_trusted_public_key(self, username: str) -> str:
        """
        The key to encrypt to. If the contact is pinned, the pinned key wins and any different
        key offered by the relay/cache is treated as an attack. Unknown names are pinned on first
        use as UNVERIFIED (the UI shows the fingerprint so the user can confirm it out of band).
        """
        from security_core import SecurityCore, public_key_fingerprint
        pinned = SecurityCore.get_contact_info(username)
        try:
            offered = self.fetch_public_key(username)
        except ValueError:
            offered = None
        if pinned:
            if offered and public_key_fingerprint(offered) != pinned["fingerprint"]:
                raise ValueError(
                    f"KEY MISMATCH for '{username}': the network offered a different key than the one you "
                    f"pinned ({pinned['fingerprint'][:17]}…). Possible impersonation — refusing to send.")
            return pinned["public_key"]
        if not offered:
            raise ValueError(f"No public key known for '{username}'.")
        if _save_to_contacts(username, offered, "relay") == "invalid":
            raise ValueError(f"'{username}' published an invalid key.")
        return offered

    def sync_global_contacts(self, skip_username: str = "") -> list:
        """
        Pulls ALL registered users from the relay, saves their public keys
        into ~/.ansx_vault/contacts/ so they appear in the receiver dropdown.
        Returns the list of usernames synced.
        """
        synced = []
        if self._relay_ok:
            try:
                data = _relay_get("/v1/identity/users")
                for u in data.get("users", []):
                    uname = u["username"]
                    if uname == skip_username:
                        continue   # don't add yourself as a contact
                    try:
                        # Fetch full profile (includes public_key)
                        profile = _relay_get(f"/v1/identity/resolve/{uname}")
                        pk = profile.get("public_key", "")
                        ip = profile.get("ip_address", "")
                        if pk and _save_to_contacts(uname, pk, "relay") in ("new", "unchanged"):
                            _merge_into_cache(uname, pk, ip)
                            synced.append(uname)
                    except Exception:
                        pass
                if synced:
                    logger.info("[Mesh] Synced %d global contacts: %s", len(synced), synced)
                return synced
            except Exception as e:
                logger.warning("[Mesh] sync_global_contacts failed: %s", e)

        # Fallback: return what we have locally
        return list(_load_cache().keys())

    def get_all_users(self) -> list:
        return self.sync_global_contacts()

    def is_registered(self, username: str) -> bool:
        if self._relay_ok:
            try:
                _relay_get(f"/v1/identity/resolve/{username}")
                return True
            except Exception:
                pass
        return username in _load_cache()


def get_web3_engine() -> ANSXMeshEngine:
    global WEB3_ENGINE
    if WEB3_ENGINE is None:
        WEB3_ENGINE = ANSXMeshEngine()
    return WEB3_ENGINE


# Keep old name for compatibility used in security_core.py
ANSXWeb3Engine = ANSXMeshEngine


import urllib.parse  # ensure available at module level
