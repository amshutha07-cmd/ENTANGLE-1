"""
cloud_dispatcher.py — A.N.Sx Vault | real shard storage on S3-compatible object stores

Nothing here is simulated. Configure one or more storage targets (AWS S3, Cloudflare R2,
Backblaze B2, MinIO, GCS/Azure via their S3-compatible gateways ...). Shard i goes to
target[i % len(targets)]. Any failure is reported as a failure; the caller decides what
to do (the UI falls back to carrying the shard inline and says so).

Config: JSON list in $ANSX_STORAGE_TARGETS (path) or ~/.ansx_vault/storage_targets.json (keep it 0600):

  [{"name": "aws-eu", "bucket": "my-bucket", "region": "eu-west-1",
    "access_key": "...", "secret_key": "...",
    "endpoint_url": null, "prefix": "ansx/"}]

Objects are named with an unguessable random id and shards are AES-GCM ciphertext,
so the storage provider learns neither file names nor content.
Presigned GET URLs are what recipients use; they last at most 7 days (S3 limit), so
they are re-issued from the stored object references each time a vault is sent.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("ANSX_STORAGE_TARGETS") or os.path.join(
    os.environ.get("ANSX_VAULT_HOME", os.path.expanduser("~/.ansx_vault")), "storage_targets.json")
PRESIGN_SECONDS = 7 * 24 * 3600


def load_targets(path: str = CONFIG_PATH) -> list[dict]:
    try:
        with open(path) as f:
            targets = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.error("[Cloud] Cannot read storage targets %s: %s", path, exc)
        return []
    ok = []
    for t in targets:
        if all(t.get(k) for k in ("name", "bucket", "access_key", "secret_key")):
            ok.append(t)
        else:
            logger.error("[Cloud] Ignoring incomplete storage target: %s", t.get("name"))
    return ok


def _make_client(t: dict):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", region_name=t.get("region") or None, endpoint_url=t.get("endpoint_url") or None,
        aws_access_key_id=t["access_key"], aws_secret_access_key=t["secret_key"],
        config=Config(retries={"max_attempts": 3}, signature_version="s3v4"),
    )


class CloudDispatcher:
    def __init__(self, targets: Optional[list[dict]] = None, client_factory: Callable = _make_client):
        self.targets = load_targets() if targets is None else targets
        self._factory = client_factory
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()
        self._vault_id = secrets.token_hex(16)
        self.uploaded_urls: dict[int, str] = {}
        self.refs: dict[int, dict] = {}       # idx -> {"target","key"} (re-presign later)
        self.errors: dict[int, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self.targets)

    def node_label(self, shard_index: int) -> str:
        return self.targets[shard_index % len(self.targets)]["name"] if self.targets else "inline"

    def _client(self, target: dict):
        with self._lock:
            if target["name"] not in self._clients:
                self._clients[target["name"]] = self._factory(target)
            return self._clients[target["name"]]

    def _presign(self, target: dict, key: str) -> str:
        return self._client(target).generate_presigned_url(
            "get_object", Params={"Bucket": target["bucket"], "Key": key}, ExpiresIn=PRESIGN_SECONDS)

    def upload_shard(self, shard_index: int, file_path: str, progress_callback) -> Optional[str]:
        """Upload one shard. Returns its presigned URL, or None on failure (see self.errors)."""
        if not self.configured:
            self.errors[shard_index] = "no storage target configured"
            return None
        target = self.targets[shard_index % len(self.targets)]
        key = f"{target.get('prefix', '')}{self._vault_id}/s{shard_index + 1:02d}.bin"
        try:
            size = max(os.path.getsize(file_path), 1)
            sent = [0]

            def _cb(n):
                sent[0] += n
                progress_callback(shard_index, min(99, int(sent[0] * 100 / size)))

            progress_callback(shard_index, 1)
            self._client(target).upload_file(file_path, target["bucket"], key, Callback=_cb)
            url = self._presign(target, key)
        except Exception as exc:
            self.errors[shard_index] = f"{type(exc).__name__}: {exc}"
            logger.error("[Cloud] Shard %02d -> %s failed: %s", shard_index + 1, target["name"], exc)
            return None
        self.uploaded_urls[shard_index] = url
        self.refs[shard_index] = {"target": target["name"], "key": key}
        progress_callback(shard_index, 100)
        logger.info("[Cloud] Shard %02d stored on '%s'", shard_index + 1, target["name"])
        return url

    def refresh_urls(self, refs: dict) -> dict[int, str]:
        """Re-issue fresh presigned URLs for previously uploaded shards (needs the same credentials)."""
        by_name = {t["name"]: t for t in self.targets}
        out = {}
        for idx, ref in refs.items():
            t = by_name.get(ref.get("target"))
            if t:
                try:
                    out[int(idx)] = self._presign(t, ref["key"])
                except Exception as exc:
                    logger.warning("[Cloud] Could not re-presign shard %s: %s", idx, exc)
        return out


# ── configuration helpers used by the Settings screen ────────────────────────────────────────────

PROVIDERS = {
    "r2":     {"label": "Cloudflare R2",  "needs": ["account_id"], "note": "Free 10 GB, no download fees."},
    "b2":     {"label": "Backblaze B2",   "needs": ["region"],     "note": "Free 10 GB, no card needed."},
    "s3":     {"label": "Amazon S3",      "needs": ["region"],     "note": "Pay as you go."},
    "custom": {"label": "Other (S3-compatible)", "needs": ["endpoint_url", "region"], "note": "MinIO, Wasabi, DigitalOcean Spaces…"},
}


def build_target(provider: str, name: str, bucket: str, access_key: str, secret_key: str, **extra) -> dict:
    """Turn what a person copies from a provider dashboard into a storage-target dict."""
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    name, bucket = name.strip(), bucket.strip()
    access_key, secret_key = access_key.strip(), secret_key.strip()
    if not all((name, bucket, access_key, secret_key)):
        raise ValueError("Name, bucket, access key and secret key are all required.")
    t = {"name": name, "bucket": bucket, "access_key": access_key, "secret_key": secret_key}
    if provider == "r2":
        acct = extra.get("account_id", "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{32}", acct):
            raise ValueError("The Cloudflare Account ID is 32 letters/digits (find it on the R2 overview page).")
        t.update(region="auto", endpoint_url=f"https://{acct.lower()}.r2.cloudflarestorage.com")
    elif provider == "b2":
        region = extra.get("region", "").strip()
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d{3}", region):
            raise ValueError("Use the region from your bucket's endpoint, for example us-west-004.")
        t.update(region=region, endpoint_url=f"https://s3.{region}.backblazeb2.com")
    elif provider == "s3":
        region = extra.get("region", "").strip()
        if not region:
            raise ValueError("Enter the bucket's region, for example eu-west-1.")
        t.update(region=region)
    else:
        endpoint = extra.get("endpoint_url", "").strip()
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError("Enter the full endpoint address, starting with https://")
        t.update(region=extra.get("region", "").strip() or "us-east-1", endpoint_url=endpoint)
    return t


def save_targets(targets: list[dict], path: str = None) -> str:
    """Write the targets file atomically, readable only by you (0600 where the OS supports it)."""
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(targets, f, indent=2)
    os.replace(tmp, path)
    return path


def public_view(target: dict) -> dict:
    """Never show or log the secret key."""
    return {k: (v if k != "secret_key" else "••••••••") for k, v in target.items()}
