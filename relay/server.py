"""
ANSX Relay v4 — identity directory + authenticated, resumable store-and-forward transfers.

What the relay is (and is not)
  * A public directory: username -> RSA public key (+ fingerprint). Registration is self-signed
    (proof of possession of the private key) and names are first-come, immutable.
  * A mailbox: senders upload an ALREADY END-TO-END ENCRYPTED blob (the ghost map) in chunks; the
    receiver is notified through their inbox, decides whether to accept, downloads (resumable),
    verifies the hash, and acknowledges — after which the relay deletes the blob.
  * It never sees plaintext, private keys, file names or NFC data, and stores nothing longer than
    the TTL. Even a fully compromised relay can only delay or drop messages, not read them or
    silently swap a receiver's key (clients pin keys, see security_core.pin_discovered_contact).

Authentication (every non-public call)
  Headers: X-ANSX-User, X-ANSX-Ts (unix s), X-ANSX-Nonce (random), X-ANSX-Sig (base64 RSA-PSS/SHA-256)
  Signed bytes: "ANSX1\\n{METHOD}\\n{path?query}\\n{ts}\\n{nonce}\\n{sha256_hex(body)}"
  Timestamps must be within ±CLOCK_SKEW; every nonce is single-use (replay protection, persisted).

Run:  uvicorn relay.server:create_app --factory --host 0.0.0.0 --port 8000
Env:  ANSX_RELAY_DATA (state dir), ANSX_MAX_TRANSFER_MB, ANSX_USER_QUOTA_MB, ANSX_TTL_HOURS, ...
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger("ansx-relay")

VERSION = "4.0.0"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass
class Config:
    data_dir: str = "relay_data"
    max_transfer_bytes: int = 128 * 1024 * 1024
    user_quota_bytes: int = 512 * 1024 * 1024
    inbox_max_pending: int = 100
    ttl_seconds: int = 7 * 24 * 3600
    chunk_size: int = 256 * 1024
    clock_skew: int = 120
    register_per_ip_hour: int = 10
    transfers_per_user_hour: int = 60
    terminal_retention_seconds: int = 30 * 24 * 3600
    cleanup_interval: int = 600

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ
        return cls(
            data_dir=e.get("ANSX_RELAY_DATA", "relay_data"),
            max_transfer_bytes=int(float(e.get("ANSX_MAX_TRANSFER_MB", 128)) * 1024 * 1024),
            user_quota_bytes=int(float(e.get("ANSX_USER_QUOTA_MB", 512)) * 1024 * 1024),
            ttl_seconds=int(float(e.get("ANSX_TTL_HOURS", 168)) * 3600),
            chunk_size=int(e.get("ANSX_CHUNK_KB", 256)) * 1024,
        )


# ─── crypto helpers ──────────────────────────────────────────────────────────

def fingerprint(public_pem: str) -> str:
    key = serialization.load_pem_public_key(public_pem.encode())
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    h = hashlib.sha256(der).hexdigest().upper()
    return " ".join(h[i:i + 8] for i in range(0, 64, 8))


def canonical(method: str, target: str, ts: str, nonce: str, body_sha: str) -> bytes:
    return f"ANSX1\n{method.upper()}\n{target}\n{ts}\n{nonce}\n{body_sha}".encode()


def verify_signature(public_pem: str, message: bytes, sig_b64: str) -> bool:
    try:
        key = serialization.load_pem_public_key(public_pem.encode())
        key.verify(base64.b64decode(sig_b64, validate=True), message,
                   padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class RateLimiter:
    """Sliding-window limiter (in-process; run a single worker)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int = 3600) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] < now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


# ─── app factory ─────────────────────────────────────────────────────────────

def create_app(cfg: Optional[Config] = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    os.makedirs(os.path.join(cfg.data_dir, "blobs"), exist_ok=True)
    db_path = os.path.join(cfg.data_dir, "relay.db")
    limiter = RateLimiter()

    @contextmanager
    def db():
        conn = sqlite3.connect(db_path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")            # per connection; WAL is set once, below
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    _init = sqlite3.connect(db_path, timeout=15)
    try:
        # WAL once, here, before any request: the mode is stored in the file. Switched per request instead, the first
        # requests to a new relay raced to switch it and SQLite failed one of them at once ("database is locked").
        _init.execute("PRAGMA journal_mode=WAL")
        _init.executescript("""
            CREATE TABLE IF NOT EXISTS identities (
                username TEXT PRIMARY KEY, public_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                created INTEGER NOT NULL, last_seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS transfers (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL,
                size INTEGER NOT NULL, sha256 TEXT NOT NULL, chunk_size INTEGER NOT NULL,
                n_chunks INTEGER NOT NULL, state TEXT NOT NULL,
                created INTEGER NOT NULL, expires INTEGER NOT NULL,
                completed INTEGER, finished INTEGER);
            CREATE INDEX IF NOT EXISTS idx_tr_rcpt ON transfers(recipient, state);
            CREATE INDEX IF NOT EXISTS idx_tr_send ON transfers(sender, state);
            CREATE TABLE IF NOT EXISTS chunks (
                transfer_id TEXT NOT NULL, idx INTEGER NOT NULL, PRIMARY KEY (transfer_id, idx));
            CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, exp INTEGER NOT NULL);
        """)
    finally:
        _init.close()

    def blob_path(tid: str, ext: str = "bin") -> str:
        return os.path.join(cfg.data_dir, "blobs", f"{tid}.{ext}")

    def rm_blob(tid: str) -> None:
        for ext in ("bin", "part"):
            try:
                os.remove(blob_path(tid, ext))
            except FileNotFoundError:
                pass

    # ── authentication ───────────────────────────────────────────────────────
    async def authenticate(request: Request, public_pem: Optional[str] = None) -> tuple[str, bytes]:
        """Returns (username, body). `public_pem` is given only for self-signed registration."""
        h = request.headers
        user, ts, nonce, sig = (h.get("x-ansx-user", ""), h.get("x-ansx-ts", ""),
                                h.get("x-ansx-nonce", ""), h.get("x-ansx-sig", ""))
        if not (NAME_RE.match(user) and ts.isdigit() and NONCE_RE.match(nonce) and sig):
            raise HTTPException(401, "Missing or malformed authentication headers.")
        if abs(time.time() - int(ts)) > cfg.clock_skew:
            raise HTTPException(401, "Timestamp outside the allowed window (check your clock).")
        body = await request.body()
        target = request.url.path + (("?" + request.url.query) if request.url.query else "")
        msg = canonical(request.method, target, ts, nonce, hashlib.sha256(body).hexdigest())

        if public_pem is None:
            with db() as c:
                row = c.execute("SELECT public_key FROM identities WHERE username=?", (user,)).fetchone()
            if not row:
                raise HTTPException(401, "Unknown user.")
            public_pem = row["public_key"]
        if not verify_signature(public_pem, msg, sig):
            raise HTTPException(401, "Bad signature.")
        with db() as c:   # only AFTER the signature is valid, so strangers cannot flood the table
            try:
                c.execute("INSERT INTO nonces(nonce, exp) VALUES (?,?)", (nonce, int(time.time()) + 2 * cfg.clock_skew + 60))
            except sqlite3.IntegrityError:
                raise HTTPException(401, "Replayed request.")
            c.execute("UPDATE identities SET last_seen=? WHERE username=?", (int(time.time()), user))
        return user, body

    def get_transfer(c, tid: str):
        if not ID_RE.match(tid):
            raise HTTPException(404, "No such transfer.")
        row = c.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such transfer.")
        return row

    def import_json(body: bytes) -> dict:
        import json
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise HTTPException(400, "Body must be a JSON object.")

    # ── lifecycle / cleanup ──────────────────────────────────────────────────
    def cleanup() -> dict:
        now = int(time.time())
        expired = purged = 0
        with db() as c:
            for r in c.execute("SELECT id FROM transfers WHERE state IN ('uploading','ready') AND expires < ?", (now,)).fetchall():
                rm_blob(r["id"])
                c.execute("UPDATE transfers SET state='expired', finished=? WHERE id=?", (now, r["id"]))
                c.execute("DELETE FROM chunks WHERE transfer_id=?", (r["id"],))
                expired += 1
            cur = c.execute("DELETE FROM transfers WHERE state NOT IN ('uploading','ready') AND finished < ?",
                            (now - cfg.terminal_retention_seconds,))
            purged = cur.rowcount
            c.execute("DELETE FROM nonces WHERE exp < ?", (now,))
        return {"expired": expired, "purged": purged}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def loop():
            while True:
                await asyncio.sleep(cfg.cleanup_interval)
                try:
                    cleanup()
                except Exception:
                    logger.exception("cleanup failed")
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="ANSX Relay", version=VERSION, lifespan=lifespan)
    app.state.cleanup = cleanup
    app.state.cfg = cfg

    # ── public ───────────────────────────────────────────────────────────────
    @app.get("/")
    def root():
        return {"node": "ANSX Relay", "status": "online", "version": VERSION}

    @app.get("/v1/limits")
    def limits():
        return {"max_transfer_bytes": cfg.max_transfer_bytes, "chunk_size": cfg.chunk_size,
                "ttl_seconds": cfg.ttl_seconds, "user_quota_bytes": cfg.user_quota_bytes}

    @app.get("/v1/identity/users")
    def list_users():
        with db() as c:
            rows = c.execute("SELECT username, created AS registered_at FROM identities ORDER BY created").fetchall()
        return {"users": [dict(r) for r in rows]}

    @app.get("/v1/identity/resolve/{username}")
    def resolve(username: str):
        with db() as c:
            row = c.execute("SELECT username, public_key, fingerprint FROM identities WHERE username=?", (username,)).fetchone()
        if not row:
            raise HTTPException(404, f"Operator '{username}' not found.")
        return dict(row)

    @app.post("/v1/identity/register", status_code=201)
    async def register(request: Request):
        ip = request.client.host if request.client else "?"
        if not limiter.allow(f"reg:{ip}", cfg.register_per_ip_hour):
            raise HTTPException(429, "Too many registrations from this address.")
        # The body carries the public key; the signature must verify against THAT key (proof of possession).
        body = await request.body()
        data = import_json(body)
        username, pem = data.get("username"), data.get("public_key")
        if not (isinstance(username, str) and NAME_RE.match(username) and ".." not in username):
            raise HTTPException(400, "Invalid username.")
        if request.headers.get("x-ansx-user") != username:
            raise HTTPException(400, "X-ANSX-User must equal the username being registered.")
        try:
            key = serialization.load_pem_public_key(pem.encode()) if isinstance(pem, str) and len(pem) <= 4096 else None
            if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                raise ValueError
            fp = fingerprint(pem)
        except Exception:
            raise HTTPException(400, "public_key must be an RSA public key (>= 2048 bits, PEM).")
        await authenticate(request, public_pem=pem)
        now = int(time.time())
        with db() as c:
            try:
                c.execute("INSERT INTO identities(username, public_key, fingerprint, created, last_seen) VALUES (?,?,?,?,?)",
                          (username, pem, fp, now, now))
            except sqlite3.IntegrityError:
                raise HTTPException(409, f"Identity '{username}' is already registered.")
        logger.info("registered %s (%s)", username, fp[:17])
        return {"status": "registered", "username": username, "fingerprint": fp}

    @app.post("/v1/identity/heartbeat")
    async def heartbeat(request: Request):
        user, _ = await authenticate(request)
        return {"status": "ok", "username": user}

    # ── transfers ────────────────────────────────────────────────────────────
    @app.post("/v1/transfers", status_code=201)
    async def create_transfer(request: Request):
        user, body = await authenticate(request)
        data = import_json(body)
        to, size, sha = data.get("to"), data.get("size"), data.get("sha256")
        if not (isinstance(to, str) and isinstance(size, int) and not isinstance(size, bool)
                and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise HTTPException(400, "Need to (str), size (int), sha256 (64 hex).")
        if not 1 <= size <= cfg.max_transfer_bytes:
            raise HTTPException(413, f"Size must be 1..{cfg.max_transfer_bytes} bytes.")
        if not limiter.allow(f"tr:{user}", cfg.transfers_per_user_hour):
            raise HTTPException(429, "Too many transfers this hour.")
        tid = secrets.token_hex(16)
        now = int(time.time())
        n_chunks = -(-size // cfg.chunk_size)
        with db() as c:
            if not c.execute("SELECT 1 FROM identities WHERE username=?", (to,)).fetchone():
                raise HTTPException(404, f"Recipient '{to}' is not registered.")
            used = c.execute("SELECT COALESCE(SUM(size),0) s FROM transfers WHERE sender=? AND state IN ('uploading','ready')", (user,)).fetchone()["s"]
            if used + size > cfg.user_quota_bytes:
                raise HTTPException(413, "Your pending-transfer quota is full; wait for deliveries or cancel some.")
            pending = c.execute("SELECT COUNT(*) n FROM transfers WHERE recipient=? AND state IN ('uploading','ready')", (to,)).fetchone()["n"]
            if pending >= cfg.inbox_max_pending:
                raise HTTPException(429, "Recipient inbox is full.")
            c.execute("INSERT INTO transfers(id,sender,recipient,size,sha256,chunk_size,n_chunks,state,created,expires) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (tid, user, to, size, sha, cfg.chunk_size, n_chunks, "uploading", now, now + cfg.ttl_seconds))
        with open(blob_path(tid, "part"), "wb") as f:
            f.truncate(size)
        return {"id": tid, "chunk_size": cfg.chunk_size, "n_chunks": n_chunks, "expires_at": now + cfg.ttl_seconds}

    @app.put("/v1/transfers/{tid}/chunks/{idx}")
    async def put_chunk(tid: str, idx: int, request: Request):
        user, body = await authenticate(request)
        with db() as c:
            t = get_transfer(c, tid)
            if t["sender"] != user:
                raise HTTPException(403, "Not your transfer.")
            if t["state"] != "uploading":
                raise HTTPException(409, f"Transfer is {t['state']}.")
            if not 0 <= idx < t["n_chunks"]:
                raise HTTPException(400, "Chunk index out of range.")
            expected = t["chunk_size"] if idx < t["n_chunks"] - 1 else t["size"] - t["chunk_size"] * (t["n_chunks"] - 1)
            if len(body) != expected:
                raise HTTPException(400, f"Chunk must be exactly {expected} bytes.")
            want = request.headers.get("x-chunk-sha256")
            if want and hashlib.sha256(body).hexdigest() != want.lower():
                raise HTTPException(422, "Chunk hash mismatch (corrupted in transit); resend.")
            if t["expires"] < time.time():
                raise HTTPException(410, "Transfer expired.")
            with open(blob_path(tid, "part"), "r+b") as f:
                f.seek(idx * t["chunk_size"])
                f.write(body)
            c.execute("INSERT OR IGNORE INTO chunks(transfer_id, idx) VALUES (?,?)", (tid, idx))
        return {"ok": True, "idx": idx}

    @app.get("/v1/transfers/{tid}")
    async def transfer_status(tid: str, request: Request):
        user, _ = await authenticate(request)
        with db() as c:
            t = get_transfer(c, tid)
            if user not in (t["sender"], t["recipient"]):
                raise HTTPException(403, "Not your transfer.")
            out = {"id": tid, "state": t["state"], "size": t["size"], "sha256": t["sha256"],
                   "n_chunks": t["n_chunks"], "chunk_size": t["chunk_size"],
                   "created": t["created"], "expires": t["expires"], "finished": t["finished"]}
            if user == t["sender"]:
                out["recipient"] = t["recipient"]
                out["received"] = [r["idx"] for r in c.execute("SELECT idx FROM chunks WHERE transfer_id=? ORDER BY idx", (tid,))]
            else:
                out["sender"] = t["sender"]
        return out

    @app.post("/v1/transfers/{tid}/complete")
    async def complete(tid: str, request: Request):
        user, _ = await authenticate(request)
        with db() as c:
            t = get_transfer(c, tid)
            if t["sender"] != user:
                raise HTTPException(403, "Not your transfer.")
            if t["state"] == "ready":
                return {"state": "ready"}          # idempotent
            if t["state"] != "uploading":
                raise HTTPException(409, f"Transfer is {t['state']}.")
            have = c.execute("SELECT COUNT(*) n FROM chunks WHERE transfer_id=?", (tid,)).fetchone()["n"]
            if have != t["n_chunks"]:
                missing = sorted(set(range(t["n_chunks"])) - {r["idx"] for r in c.execute("SELECT idx FROM chunks WHERE transfer_id=?", (tid,))})
                raise HTTPException(409, f"Missing chunks: {missing[:20]}")
            h = hashlib.sha256()
            with open(blob_path(tid, "part"), "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != t["sha256"]:
                # Return (don't raise) so the reset below is committed rather than rolled back.
                c.execute("DELETE FROM chunks WHERE transfer_id=?", (tid,))
                with open(blob_path(tid, "part"), "wb") as f:
                    f.truncate(t["size"])
                return JSONResponse({"detail": "Whole-file hash mismatch; upload discarded, please resend."}, status_code=422)
            os.replace(blob_path(tid, "part"), blob_path(tid, "bin"))
            c.execute("UPDATE transfers SET state='ready', completed=? WHERE id=?", (int(time.time()), tid))
        return {"state": "ready"}

    def _summaries(rows, extra):
        return [dict({"id": r["id"], "size": r["size"], "sha256": r["sha256"], "created": r["created"],
                      "expires": r["expires"], "state": r["state"]}, **extra(r)) for r in rows]

    @app.get("/v1/inbox")
    async def inbox(request: Request):
        user, _ = await authenticate(request)
        with db() as c:
            rows = c.execute("""SELECT t.*, i.fingerprint AS sender_fp FROM transfers t
                                LEFT JOIN identities i ON i.username = t.sender
                                WHERE t.recipient=? AND t.state='ready' AND t.expires>=? ORDER BY t.completed, t.rowid""",
                             (user, int(time.time()))).fetchall()
        return {"items": _summaries(rows, lambda r: {"from": r["sender"], "sender_fingerprint": r["sender_fp"]})}

    @app.get("/v1/outbox")
    async def outbox(request: Request):
        user, _ = await authenticate(request)
        with db() as c:
            rows = c.execute("SELECT * FROM transfers WHERE sender=? ORDER BY created DESC, rowid DESC LIMIT 50", (user,)).fetchall()
        return {"items": _summaries(rows, lambda r: {"to": r["recipient"], "finished": r["finished"]})}

    @app.get("/v1/transfers/{tid}/download")
    async def download(tid: str, request: Request):
        user, _ = await authenticate(request)
        with db() as c:
            t = get_transfer(c, tid)
            if t["recipient"] != user:
                raise HTTPException(403, "Not your transfer.")
            if t["state"] != "ready":
                raise HTTPException(409, f"Transfer is {t['state']}.")
            if t["expires"] < time.time():
                raise HTTPException(410, "Transfer expired.")
        size, start, end, status = t["size"], 0, t["size"] - 1, 200
        rng = request.headers.get("range")
        if rng:
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
            if not m:
                raise HTTPException(416, "Unsupported Range.")
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
        path = blob_path(tid)

        def stream():
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    block = f.read(min(1 << 20, left))
                    if not block:
                        break
                    left -= len(block)
                    yield block

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "X-Transfer-SHA256": t["sha256"]}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(stream(), status_code=status, media_type="application/octet-stream", headers=headers)

    def _finish(tid: str, user: str, role: str, new_state: str, allowed: tuple):
        with db() as c:
            t = get_transfer(c, tid)
            if t[role] != user:
                raise HTTPException(403, "Not your transfer.")
            if t["state"] not in allowed:
                raise HTTPException(409, f"Transfer is {t['state']}.")
            c.execute("UPDATE transfers SET state=?, finished=? WHERE id=?", (new_state, int(time.time()), tid))
            c.execute("DELETE FROM chunks WHERE transfer_id=?", (tid,))
        rm_blob(tid)
        return {"state": new_state}

    @app.post("/v1/transfers/{tid}/ack")
    async def ack(tid: str, request: Request):
        user, _ = await authenticate(request)
        return _finish(tid, user, "recipient", "delivered", ("ready",))

    @app.post("/v1/transfers/{tid}/reject")
    async def reject(tid: str, request: Request):
        user, _ = await authenticate(request)
        return _finish(tid, user, "recipient", "rejected", ("ready",))

    @app.delete("/v1/transfers/{tid}")
    async def cancel(tid: str, request: Request):
        user, _ = await authenticate(request)
        return _finish(tid, user, "sender", "cancelled", ("uploading", "ready"))

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app
