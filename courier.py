"""
courier.py — A.N.Sx Vault | manifest building, re-wrapping for a receiver, and shard retrieval

Manifest (JSON, lives ONLY inside an RSA+AES-GCM encrypted ghost map):
  {"version": 2, "original_file", "ephemeral_key", "shard_count": 12,
   "shard_sha256": {"1": hex, ...},
   "cloud_urls":   {"0": https-url, ...}      # 0-based shard index -> presigned URL
   "cloud_refs":   {"0": {"target","key"}}    # lets the owner re-presign
   "shard_payload": {"fragment_12.ansx": base64, ...}}   # shards carried inline
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

SHARD_NAME_RE = re.compile(r"^fragment_(1[0-2]|[1-9])\.ansx$")
K_DATA = 8
_MAX_SHARD_BYTES = 2 * 1024 ** 3


class CourierError(RuntimeError):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(original_file: str, key: str, shard_dir: str,
                   cloud_urls: dict, cloud_refs: dict) -> dict:
    """Shards that made it to cloud storage are referenced; every other shard travels inline."""
    hashes, inline = {}, {}
    present = 0
    for i in range(1, 13):
        path = os.path.join(shard_dir, f"fragment_{i}.ansx")
        if not os.path.exists(path):
            continue
        present += 1
        hashes[str(i)] = _sha256(path)
        if (i - 1) not in cloud_urls:
            with open(path, "rb") as f:
                inline[f"fragment_{i}.ansx"] = base64.b64encode(f.read()).decode()
    if present < 12:
        raise CourierError(f"Only {present}/12 shards found in {shard_dir}.")
    return {
        "version": 2, "original_file": os.path.basename(original_file), "ephemeral_key": key,
        "shard_count": 12, "shard_sha256": hashes,
        "cloud_urls": {str(k): v for k, v in cloud_urls.items()},
        "cloud_refs": {str(k): v for k, v in cloud_refs.items()},
        "shard_payload": inline,
    }


def refresh_manifest_urls(manifest: dict, dispatcher) -> dict:
    """Re-issue expiring presigned URLs (only possible if this machine holds the storage credentials)."""
    refs = manifest.get("cloud_refs") or {}
    if refs and dispatcher is not None and dispatcher.configured:
        fresh = dispatcher.refresh_urls(refs)
        manifest["cloud_urls"].update({str(k): v for k, v in fresh.items()})
    return manifest


def _download(url: str, dest: str, expected_sha: Optional[str]) -> bool:
    import requests
    low = url.lower()
    local_dev = low.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
    if not (low.startswith("https://") or local_dev):
        logger.warning("[Fetch] Refusing non-HTTPS shard URL.")
        return False
    h = hashlib.sha256()
    total = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    total += len(chunk)
                    if total > _MAX_SHARD_BYTES:
                        raise CourierError("shard too large")
                    h.update(chunk)
                    f.write(chunk)
    except Exception as exc:
        logger.warning("[Fetch] Download failed: %s", exc)
        return False
    if expected_sha and h.hexdigest() != expected_sha:
        logger.error("[Fetch] Hash mismatch for %s; discarding.", os.path.basename(dest))
        os.remove(dest)
        return False
    return True


def fetch_shards(manifest: dict, dest_dir: str) -> int:
    """
    Materialise the shards described by `manifest` into a FRESH `dest_dir`.
    Inline shards are validated by name (no path traversal) and hash; cloud shards are
    downloaded over HTTPS and hash-checked. Returns the number of valid shards.
    Raises CourierError if fewer than K are valid.
    """
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, mode=0o700)
    hashes = manifest.get("shard_sha256") or {}
    good = set()

    for fname, b64 in (manifest.get("shard_payload") or {}).items():
        m = SHARD_NAME_RE.match(fname)
        if not m:
            raise CourierError(f"Manifest contains an illegal shard name: {fname!r}")
        data = base64.b64decode(b64)
        idx = m.group(1)
        if hashes.get(idx) and hashlib.sha256(data).hexdigest() != hashes[idx]:
            logger.error("[Fetch] Inline shard %s failed its hash check; skipped.", fname)
            continue
        with open(os.path.join(dest_dir, fname), "wb") as f:
            f.write(data)
        good.add(int(idx))

    for k, url in (manifest.get("cloud_urls") or {}).items():
        idx = int(k) + 1
        if idx in good or not 1 <= idx <= 12:
            continue
        if _download(url, os.path.join(dest_dir, f"fragment_{idx}.ansx"), hashes.get(str(idx))):
            good.add(idx)

    if len(good) < K_DATA:
        raise CourierError(f"Only {len(good)} valid shards could be retrieved; {K_DATA} are required.")
    return len(good)
