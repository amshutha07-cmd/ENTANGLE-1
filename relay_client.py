"""
relay_client.py — A.N.Sx Vault | client for the ANSX relay (relay/server.py)

No Qt in here: the GUI wraps it in threads (transfer.py), tests drive it directly.

  * Every request is signed with the operator's RSA key (RSA-PSS/SHA-256) over
    method, path, timestamp, single-use nonce and body hash; the relay rejects replays.
  * Uploads are chunked and RESUMABLE: on any failure the client asks the relay which chunks
    it already holds and sends only the rest. Each chunk carries its SHA-256.
  * Downloads use HTTP Range into a .part file, are RESUMABLE, and are verified against the
    SHA-256 the sender committed to BEFORE the receiver acknowledges (and the relay deletes) the blob.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Callable, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

Progress = Callable[[int, int], None]          # (bytes_done, bytes_total)


class RelayError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(f"[{status}] {detail}")

    @property
    def retryable(self) -> bool:
        return self.status in (0, 408, 425, 429, 500, 502, 503, 504)


class Cancelled(Exception):
    pass


def _canonical(method: str, target: str, ts: str, nonce: str, body_sha: str) -> bytes:
    return f"ANSX1\n{method.upper()}\n{target}\n{ts}\n{nonce}\n{body_sha}".encode()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class RelayClient:
    def __init__(self, base_url: str, username: Optional[str] = None, private_pem: Optional[str] = None,
                 timeout: tuple = (8, 60), retries: int = 4, session: Optional[requests.Session] = None):
        self.base = base_url.rstrip("/")
        self.username = username
        self._key = (serialization.load_pem_private_key(private_pem.encode(), password=None)
                     if private_pem else None)
        self.timeout, self.retries = timeout, retries
        self.http = session or requests.Session()

    # ── signing / transport ──────────────────────────────────────────────────
    def _sign_headers(self, method: str, target: str, body: bytes, username: Optional[str] = None) -> dict:
        if self._key is None:
            raise RelayError(401, "No private key loaded (log in with your NFC card first).")
        ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
        msg = _canonical(method, target, ts, nonce, hashlib.sha256(body).hexdigest())
        sig = self._key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        return {"X-ANSX-User": username or self.username or "", "X-ANSX-Ts": ts, "X-ANSX-Nonce": nonce,
                "X-ANSX-Sig": base64.b64encode(sig).decode()}

    def _request(self, method: str, target: str, *, body: bytes = b"", auth: bool = True,
                 headers: Optional[dict] = None, stream: bool = False, cancel: Optional[Callable] = None):
        last: Optional[RelayError] = None
        for attempt in range(self.retries + 1):
            if cancel and cancel():
                raise Cancelled()
            h = dict(headers or {})
            if auth:
                h.update(self._sign_headers(method, target, body))   # fresh nonce every attempt
            try:
                r = self.http.request(method, self.base + target, data=body or None, headers=h,
                                      timeout=self.timeout, stream=stream)
            except requests.RequestException as exc:
                last = RelayError(0, f"network error: {exc}")
            else:
                if r.status_code < 400:
                    return r
                try:
                    detail = r.json().get("detail", r.text)
                except ValueError:
                    detail = r.text[:200]
                last = RelayError(r.status_code, str(detail))
                r.close()
            if not last.retryable or attempt == self.retries:
                raise last
            time.sleep(min(0.5 * 2 ** attempt, 8))
        raise last  # pragma: no cover

    def _json(self, method: str, target: str, payload: Optional[dict] = None, **kw) -> dict:
        body = json.dumps(payload).encode() if payload is not None else b""
        headers = {"Content-Type": "application/json"} if payload is not None else None
        return self._request(method, target, body=body, headers=headers, **kw).json()

    # ── directory ────────────────────────────────────────────────────────────
    def ping(self) -> bool:
        try:
            self.http.get(self.base + "/", timeout=(4, 6)).raise_for_status()
            return True
        except requests.RequestException:
            return False

    def limits(self) -> dict:
        return self._json("GET", "/v1/limits", auth=False)

    def register(self, public_pem: str) -> dict:
        return self._json("POST", "/v1/identity/register", {"username": self.username, "public_key": public_pem})

    def heartbeat(self) -> dict:
        return self._json("POST", "/v1/identity/heartbeat", {})

    def resolve(self, username: str) -> dict:
        return self._json("GET", f"/v1/identity/resolve/{username}", auth=False)

    def users(self) -> list[str]:
        return [u["username"] for u in self._json("GET", "/v1/identity/users", auth=False)["users"]]

    # ── sending ──────────────────────────────────────────────────────────────
    def send_file(self, path: str, to: str, progress: Optional[Progress] = None,
                  cancel: Optional[Callable[[], bool]] = None, resume_id: Optional[str] = None) -> str:
        """Upload `path` to `to`'s inbox. Returns the transfer id once the relay has verified the whole file."""
        size, digest = os.path.getsize(path), sha256_file(path)
        if resume_id:
            info = self._json("GET", f"/v1/transfers/{resume_id}")
            if info["sha256"] != digest or info["size"] != size or info.get("recipient") != to or info["state"] != "uploading":
                raise RelayError(409, "The pending upload does not match this file/recipient; start a new send.")
            tid, chunk = resume_id, info["chunk_size"]
        else:
            created = self._json("POST", "/v1/transfers", {"to": to, "size": size, "sha256": digest}, cancel=cancel)
            tid, chunk = created["id"], created["chunk_size"]
        self.last_transfer_id = tid

        done = set(self._json("GET", f"/v1/transfers/{tid}")["received"])
        sent = min(size, len(done) * chunk)
        with open(path, "rb") as f:
            idx = 0
            while True:
                if cancel and cancel():
                    raise Cancelled()
                block = f.read(chunk)
                if not block:
                    break
                if idx not in done:
                    self._request("PUT", f"/v1/transfers/{tid}/chunks/{idx}", body=block,
                                  headers={"Content-Type": "application/octet-stream",
                                           "X-Chunk-SHA256": hashlib.sha256(block).hexdigest()}, cancel=cancel)
                    sent += len(block)
                if progress:
                    progress(min(sent, size), size)
                idx += 1
        self._json("POST", f"/v1/transfers/{tid}/complete", {})
        if progress:
            progress(size, size)
        return tid

    def outbox(self) -> list[dict]:
        return self._json("GET", "/v1/outbox")["items"]

    def cancel_transfer(self, tid: str) -> dict:
        return self._json("DELETE", f"/v1/transfers/{tid}")

    def status(self, tid: str) -> dict:
        return self._json("GET", f"/v1/transfers/{tid}")

    # ── receiving ────────────────────────────────────────────────────────────
    def inbox(self) -> list[dict]:
        return self._json("GET", "/v1/inbox")["items"]

    def download(self, item: dict, dest_path: str, progress: Optional[Progress] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> str:
        """
        Download an inbox item to `dest_path` (resuming any .part file), verify its SHA-256,
        and only then acknowledge it. Raises RelayError(422) on a hash mismatch (nothing is kept).
        """
        tid, size, want = item["id"], item["size"], item["sha256"]
        part = dest_path + ".part"
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:
            os.remove(part)
            have = 0
        stalls = 0
        while have < size:
            before = have
            headers = {"Range": f"bytes={have}-"} if have else {}
            r = self._request("GET", f"/v1/transfers/{tid}/download", headers=headers, stream=True, cancel=cancel)
            try:
                with open(part, "ab" if have else "wb") as out:
                    for block in r.iter_content(1 << 16):
                        if cancel and cancel():
                            raise Cancelled()
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(have, size)
            except requests.RequestException as exc:      # dropped mid-stream: loop resumes with Range
                logger.warning("download interrupted at %d/%d: %s", have, size, exc)
                time.sleep(1)
            finally:
                r.close()
            stalls = stalls + 1 if have == before else 0
            if stalls >= 5:
                raise RelayError(0, "Download stalled (no progress after 5 attempts).")
        if sha256_file(part) != want:
            os.remove(part)
            raise RelayError(422, "Downloaded data does not match the sender's SHA-256; discarded.")
        os.replace(part, dest_path)
        self._json("POST", f"/v1/transfers/{tid}/ack", {})
        return dest_path

    def reject(self, tid: str) -> dict:
        return self._json("POST", f"/v1/transfers/{tid}/reject", {})
