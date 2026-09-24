#!/usr/bin/env python3
"""
check_storage.py — verify your shard storage targets (used by the Settings screen and the command line).

    python check_storage.py                 # every target in ~/.ansx_vault/storage_targets.json
    python check_storage.py path/to.json

For each target: upload a tiny test object, download it through a presigned link (exactly what a
receiver does), confirm the bucket is NOT publicly readable, then try to delete the test object.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys

import requests

import cloud_dispatcher

HINTS = {
    "InvalidAccessKeyId": "The access key is wrong (typo, or from a different provider or account).",
    "SignatureDoesNotMatch": "The secret key is wrong or has stray spaces, or the region/endpoint does not match.",
    "AccessDenied": "The key is valid but not allowed to write to this bucket. Recreate it with read and write access to this bucket.",
    "NoSuchBucket": "The bucket name is wrong (names are case-sensitive) or it belongs to another account or region.",
    "AuthorizationHeaderMalformed": "The region is wrong for this bucket. Use the region shown in your provider's dashboard.",
    "PermanentRedirect": "Wrong region or endpoint for this bucket.",
    "Could not connect": "Cannot reach the storage address. Check the spelling, your internet connection, or a VPN/firewall.",
    "InvalidBucketName": "This bucket name is not valid for the provider.",
}


def hint_for(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}"
    for key, hint in HINTS.items():
        if key in text:
            return hint
    return "Unexpected error; the technical message above may help your provider's support."


def run_checks(target: dict) -> tuple[bool, list[tuple[str, str]]]:
    """Returns (all_ok, [(status, message)]) with status in ok | fail | warn | info."""
    out: list[tuple[str, str]] = []
    bucket = target["bucket"]
    try:
        client = cloud_dispatcher._make_client(target)
    except Exception as exc:
        return False, [("fail", f"Could not set up the connection: {exc}")]
    key = f"{target.get('prefix', '')}_ansx_check_{secrets.token_hex(6)}.bin"
    body = os.urandom(2048)

    try:
        client.put_object(Bucket=bucket, Key=key, Body=body)
        out.append(("ok", "Upload works"))
    except Exception as exc:
        return False, [("fail", f"Upload failed: {exc}"), ("info", hint_for(exc))]

    ok = True
    try:
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=300)
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and hashlib.sha256(r.content).digest() == hashlib.sha256(body).digest():
            out.append(("ok", "Download links work (this is what receivers use)"))
        else:
            ok = False
            out.append(("fail", f"Download link returned HTTP {r.status_code}. The key probably lacks read permission."))
        local = url.startswith(("http://127.0.0.1", "http://localhost"))
        if url.startswith("http://") and not local:
            ok = False
            out.append(("fail", "Links are plain http://. Receivers require https:// for real storage."))
        plain = requests.get(url.split("?")[0], timeout=20)
        if plain.status_code == 200:
            ok = False
            out.append(("fail", "SECURITY: the file is readable without a signature. Make the bucket private."))
        else:
            out.append(("ok", "Bucket is private"))
    except Exception as exc:
        ok = False
        out.append(("fail", f"Download check failed: {exc}"))
        out.append(("info", hint_for(exc)))

    try:
        client.delete_object(Bucket=bucket, Key=key)
        out.append(("ok", "Test file cleaned up"))
    except Exception:
        out.append(("warn", "Could not delete the test file (fine if your key cannot delete)."))
    return ok, out


def check(target: dict) -> bool:
    print(f"\n── {target['name']}  (bucket '{target['bucket']}', {target.get('endpoint_url') or 'AWS S3'}) ──")
    ok, lines = run_checks(target)
    icon = {"ok": "✔", "fail": "✘", "warn": "•", "info": "→"}
    for status, msg in lines:
        print(f"  {icon[status]} {msg}")
    return ok


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else cloud_dispatcher.CONFIG_PATH
    if not os.path.exists(path):
        print(f"No storage config at {path}\nAdd one in the app's Settings screen, or see SETUP.md (Part B).")
        return 2
    try:
        with open(path) as f:
            raw = json.load(f)
    except ValueError as exc:
        print(f"{path} is not valid JSON: {exc}")
        return 2
    targets = cloud_dispatcher.load_targets(path)
    if not targets:
        print("No complete targets: each needs name, bucket, access_key and secret_key.")
        return 2
    if len(targets) != len(raw):
        print("Some targets are incomplete and were skipped.")
    results = [check(t) for t in targets]
    print(f"\n{sum(results)}/{len(results)} target(s) fully working.")
    if len(targets) < 2:
        print("Tip: add a second provider so no single company holds all your pieces.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
