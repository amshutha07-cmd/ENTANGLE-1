# A.N.Sx Vault — Security model & implementation status

This file is the source of truth for what the code does. If a README, slide or patent
draft says something stronger, this file wins.

## Data path (what actually protects a file)

```
file → zlib → AES-256-GCM → Reed-Solomon RS(12,8) over GF(2^8) → 12 self-describing shards
```

| Layer | What it gives you |
|---|---|
| AES-256-GCM (`shatter_engine.cpp`) | Confidentiality **and** integrity. Wrong key or any tampering → `-11`, no output written. |
| Key = HKDF-SHA256(salt, C1 ‖ C2) | C1 = per-vault random 256-bit key (travels in the encrypted ghost map). C2 = optional second factor (unused for transfers, see below). **A key exists; it is derived, never stored on the shards.** |
| RS(12,8) | Availability: any 8 of 12 shards rebuild the payload; corrupt shards are detected by CRC32 and skipped. |
| Keyed GF polynomial + evaluation points | The codec differs per vault (one of the 16 primitive polynomials of degree 8; keyed shard x-coordinates). **This is not a security boundary** — the ciphertext is what is protected. |

Shard format v2 is self-describing (52-byte header: magic, version, index, K, N, salt, length, CRC32).

## Transport (the relay)

`sender ──HTTPS──▶ relay (mailbox) ◀──HTTPS── receiver`, with the relay seeing only ciphertext.

1. **Directory.** Each user self-registers `name → RSA public key` on the relay, signing the request with the matching private key (proof of possession). Names are first-come and immutable; nobody can overwrite another user's key.
2. **Every request is signed** (RSA-PSS/SHA-256 over method, path, timestamp, single-use nonce, body hash). Replayed, stale, forged or re-targeted requests are rejected.
3. **Send.** The sender re-wraps the vault manifest for the receiver (RSA-OAEP + AES-GCM, inside a PNG), then uploads it in 256 KB chunks. Each chunk carries a SHA-256, the relay verifies the whole-file hash, and an interrupted upload **resumes** from the chunks the relay already has.
4. **Consent.** The receiver's inbox shows sender, size and key status (*verified / unverified / KEY CHANGED*). Nothing is downloaded or opened until they accept; they can decline, which deletes it from the relay.
5. **Receive.** Download uses HTTP Range (**resumable**), is checked against the hash the sender committed to, and only then is it acknowledged, which makes the relay delete the blob. The sender's app then shows *delivered*.
6. **Housekeeping.** Blobs expire after 7 days (configurable), per-user quotas and rate limits apply, and finished transfers are purged after 30 days.

Ghost maps may still be delivered out of band (USB, chat, e-mail); the Receive screen has an *Open a ghost map file…* button for that.

**What a hostile relay can do:** learn who exchanges data with whom, when and how large; drop or delay messages; lie about who is registered (clients pin keys on first use and reject changes). **What it cannot do:** read contents, forge messages from a user, or swap the key of a contact you have already pinned.

Shards themselves are stored on S3-compatible targets you configure (`cloud_dispatcher.py`); anything not uploaded travels inline in the ghost map and the UI says so. There are no simulated uploads. Every shard is SHA-256 checked on retrieval, downloads must be HTTPS, and manifest shard names are validated (no path traversal).

## Identity & login

- Private keys (RSA-4096, Ethereum wallet) are stored **only AES-GCM-wrapped**. The wrap key derives from PBKDF2-SHA256 (600k) over `NFC seed ‖ (random salt ‖ platform secret)`. Without the right card **and** this machine's platform secret, the keys cannot be decrypted; a wrong card fails cryptographically, not just in the UI.
- Decrypted keys live in memory only after a successful login and are dropped on lock/logout.
- Legacy plaintext identities are migrated (and encrypted) on the first successful login.
- The geolocation lock and MAC-address binding were removed from all security decisions (spoofable, flaky, and locked users out when the IP lookup failed).

### Platform secret — be precise about what you have

`platform_secret.backend_name()` reports what is really in use. Backends, best first:

| Backend | Reality |
|---|---|
| `tpm2` | Linux-only TPM 2.0 via `tpm2-tools` (no Windows/macOS TPM backend yet). **Implemented but NOT tested on real hardware here.** |
| `keyring` | The OS keystore: Windows Credential Locker, macOS Keychain or Linux Secret Service. OS-protected, not a discrete security chip. **Not a TPM and must not be described as one.** |
| `file` | 0600 file. Last resort; a warning is logged. |

There is **no Secure Enclave integration** yet (Python cannot reach it without a native helper).

## Sign-in options and local privacy

- **NFC card** (Arduino + PN532) or **passphrase** (min. 12 characters; no hardware needed). Passphrase mode is weaker against an attacker who
  can guess phrases offline, but keys are still encrypted with PBKDF2 (600k iterations) plus this machine's protected secret. There is no recovery: losing the
  card/passphrase means the identity's files cannot be opened.
- Several people can share one computer: each person's protected-file list is private to them.
- The app **locks itself when idle** (default 10 minutes; configurable) and wipes decrypted keys from memory on lock.
- File names inside a package are chosen by the sender, so they are **sanitised before saving** (no folders, control characters, or Windows-reserved names) and never overwrite an existing file.
- Received/restored files land in `Downloads/A.N.Sx Vault`. Temporary working files are deleted even when an operation fails.

## Trust model

- Contacts are **pinned on first use** with a SHA-256 fingerprint. A later different key for the same name (relay, LAN or chain) is **rejected**. Keys learned from the network are marked *unverified*; only a manual `.ansx_id` import counts as verified. Compare fingerprints out of band for anything important.
- The LAN listener no longer writes to your contacts blindly: it validates input and uses the packet's real source address.
- **The blockchain layer is optional and off by default.** The relay is the directory and mailbox. `ANSXRegistry.sol` / `SessionBroker.sol` (hardened, tested on a local EVM) remain for anyone who wants a decentralized directory; if used, everything on-chain is public.
- Operator/contact names are validated (`[A-Za-z0-9._-]{1,32}`); they used to become file paths unchecked.

If you do enable the contracts, redeploy them (`python3 deploy_contracts.py`, needs Amoy test MATIC); the ABIs changed.

## Known limitations (not fixed — hardware/infra dependent)

1. **NFC card is a static secret.** The PN532 sketch stores 16 bytes in a MIFARE Classic block. Firmware v2 locks that sector with a per-card key (HMAC of the card UID with the platform secret), which stops ordinary readers and phone apps, but Crypto1 is broken: anyone who gets hold of the card with a Proxmark-class tool can recover the key and copy it. Proper fix: NTAG 424 DNA (AES-128 challenge-response with rolling counter) or a DESFire/JavaCard applet, with the secret never leaving the chip. This needs new cards and firmware.
2. **No TPM/Secure Enclave verified.** See above. "Two hardware roots" is not something this code achieves today.
3. **Presigned URLs last ≤7 days.** They are re-issued from stored references when the owner sends or re-opens a vault (needs the same storage credentials). A shared vault whose recipient waits >7 days needs a fresh send.
4. **Solid or noisy carrier images are detectable by steganalysis.** Confidentiality does not depend on hiding; treat the LSB layer as cosmetic.
5. **Relay metadata.** Even with E2E encryption the relay sees sender, receiver, time and size. Traffic-analysis resistance (padding, mixnets) is out of scope. Notifications are by polling every ~8 s (no push).
7. **No key rotation / account recovery yet.** A registered name is bound to its key forever; losing the key means choosing a new name.
8. **The relay is a single point of availability.** It cannot read or forge, but if it is down nobody can send. Run it with backups and monitoring.
6. Ghost maps embed inline shards, so payloads with no cloud storage configured are limited by image capacity (the carrier grows automatically; very large files need cloud targets).

## Storage targets

`~/.ansx_vault/storage_targets.json` (chmod 600) or `$ANSX_STORAGE_TARGETS`:

```json
[{"name": "aws-eu",  "bucket": "my-bucket", "region": "eu-west-1",
  "access_key": "…", "secret_key": "…"},
 {"name": "r2-eu",   "bucket": "ansx", "endpoint_url": "https://<acct>.r2.cloudflarestorage.com",
  "access_key": "…", "secret_key": "…"}]
```

Shard *i* goes to target *i mod len(targets)*. Use least-privilege keys (PutObject/GetObject on one prefix).

## Run the whole system

```bash
pip install -r requirements.txt
python build_engine.py
uvicorn relay.server:create_app --factory --port 8000   # or deploy relay/ (see relay/README.md)
python main.py                                           # each user; set ANSX_RELAY_URL for a remote relay
```

## Build & test

```bash
python build_engine.py       # builds the native engine for THIS OS (needs a C++17 compiler, OpenSSL 3, zlib)
pip install -r requirements-dev.txt
python -m pytest tests -q    # engine, identity, ghost map, cloud, contracts (local EVM)
pyinstaller ANSxVault.spec   # optional: one-folder app bundle for the current OS
```

## Platform notes

The project is OS-neutral (Windows, Linux, macOS): pure-Python code plus one portable C++17 library.

| | Windows | Linux | macOS |
|---|---|---|---|
| Native engine | `shatter.dll` (MSYS2 g++, clang++ or MSVC) | `libshatter.so` | `libshatter.dylib` |
| Platform-secret backend | Credential Locker | Secret Service, or TPM2 | Keychain |
| Serial port for the PN532 reader | `COMx` | `/dev/ttyACM*`, `/dev/ttyUSB*` | `/dev/cu.usb*` |
| File permissions (0700/0600) | Not enforced (relies on your user profile ACLs) | Enforced | Enforced |

Windows tips: install OpenSSL 3 and zlib through MSYS2 (`pacman -S mingw-w64-ucrt-x86_64-openssl mingw-w64-ucrt-x86_64-zlib`) or vcpkg and point `OPENSSL_DIR` at the prefix. On Linux headless servers without a Secret Service, the platform secret falls back to a file and logs a warning.
