# A.N.Sx Vault — setup guide (from zero to a working two-person transfer)

Do the parts **in order**. Part A proves everything works on one computer with no accounts.
Parts B–D make it real (cloud storage, a public relay, two users).

---

## What works today, and what does not (read this first)

| Item | Status |
|---|---|
| Shard engine (encrypt + Reed-Solomon), identity, ghost maps, relay, send/receive, resume, consent, the whole UI | **Works. Covered by 53 automated tests, including a full alice→bob journey through the real screens against a real relay.** |
| Cloud bucket upload/presign | **Works with the real `boto3` client against an S3 emulator. Not yet tested against a real R2/S3 account** (I have no credentials). |
| Bucket configured | **No.** You must create one (Part B). Until then shards travel *inside* the ghost map: it works but is limited by image size, and the UI says "carried inline". |
| Relay deployed | **No.** The old `ansxvault.onrender.com` runs the *old* API and will not work. Locally it works; publicly you must deploy it (Part C). |
| `relay/Dockerfile`, `render.yaml` | **Written, never built** (no Docker here). Expect to fix small things on first build. |
| Windows / Linux | **Code is portable but I only ran it on macOS.** |
| Real human clicking the UI | **Not done on real hardware.** 53 automated tests (including 7 that drive the real window offscreen) pass, and every screen was rendered and inspected, but nobody has yet used it on a real desktop. |
| NFC card | Works with your Arduino+PN532. With reader firmware v2 each card is locked with its own key, so an ordinary reader can't read it; a specialist Mifare-cracking tool still can. **Real protection needs NTAG 424 DNA cards.** |
| TPM backend | Written for Linux, **never run on a real TPM**. On Windows/macOS the OS keychain is used (that is not a TPM). |
| Blockchain contracts | **Optional, off by default.** Compiled and tested on a local EVM only; not deployed. |
| Patent PDF/TXT | Stale. Re-export from the corrected `.md` before filing. |

---

## Part A — run everything on one computer (30 min, no accounts)

1. **Install Python 3.11+ and a C++ compiler + OpenSSL + zlib**
   - macOS: `xcode-select --install` then `brew install openssl@3`
   - Ubuntu/Debian: `sudo apt install build-essential libssl-dev zlib1g-dev python3-venv`
   - Windows: install MSYS2, then in its *UCRT64* shell: `pacman -S mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-openssl mingw-w64-ucrt-x86_64-zlib`, and add `C:\msys64\ucrt64\bin` to PATH.
2. **Get the code and a virtual environment**
   ```bash
   cd A.N.SXVault1
   python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   ```
3. **Build the native engine** — `python build_engine.py`. Expect `built libshatter.<ext> (engine v2)`.
   On Windows it also copies the OpenSSL and zlib DLLs next to `shatter.dll` (Python does not look for them on PATH).
   If the app says *"Could not find module …shatter.dll (or one of its dependencies)"*, run `python build_engine.py` again.
   If it says OpenSSL not found: set `OPENSSL_DIR` to the folder containing `include/` and `lib/` (macOS Homebrew: `export OPENSSL_DIR=$(brew --prefix openssl@3)`).
4. **Run the tests** — `python -m pytest tests -q`. Everything should pass (≈30 s). If not, stop and fix that first.
5. **Start a local relay** in a second terminal: `uvicorn relay.server:create_app --factory --port 8000`.
   Check: open http://127.0.0.1:8000/ — you should see `{"node":"ANSX Relay","status":"online",...}`.
6. **Start the app** — `python main.py`. The first screen walks you through creating your identity:
   choose a name, choose **NFC card** or **Passphrase** (no hardware needed), and the app creates your keys.
   With no relay set, it uses `http://127.0.0.1:8000` (change it any time in **Settings → Connection**).

---

## The fastest path (free, on your own computer)

```bash
pip3 install -r requirements.txt
python3 build_engine.py
brew install cloudflared            # Windows: winget install --id Cloudflare.cloudflared
python3 run_relay.py --tunnel       # keep this window open
```

`run_relay.py --tunnel` starts the relay **and** a free public HTTPS address, checks that the address works from the
internet, saves it as this computer's setting, and prints it. Everyone else pastes that address into **Settings → Connection**.
(The address changes each time you restart it. If the app on the same computer can't reach it for a minute or two, your
computer's DNS hasn't learned the new name yet; the launcher tells you how to flush it.)

**Want an address that never changes?** Use Tailscale Funnel instead of Cloudflare:

```bash
brew install --cask tailscale-app   # or tailscale.com/download; open it and sign in (version 1.52 or newer)
python3 run_relay.py --tailscale    # keep this window open
```

The address is `https://<this-computer>.<your-tailnet>.ts.net` and stays the same across restarts, so people enter it
once. Only the computer running the relay needs Tailscale; everyone else just uses the address. The first time, Funnel may
need to be allowed for your tailnet: the launcher prints Tailscale's link to switch it on.

Once it works, you can make it start by itself whenever you log in (macOS, Windows and Linux):

```bash
python3 run_relay.py --tailscale --install-autostart    # starts now and at every login; restarts if it stops
python3 run_relay.py --remove-autostart                 # undo
```

The log is in `~/Library/Logs/ANSX Relay/relay.log` on a Mac (Windows: `.ansx_vault\relay.log` in your user folder; Linux:
`journalctl --user -u ansx-relay`). The relay is only reachable while this computer is on and awake.

The permanent address is also written to `default_config.json` in this folder. The app uses it when nobody has entered
an address yet, and `pyinstaller ANSxVault.spec` builds it into the installer, so people you give that installer to never
type an address. The file is in `.gitignore`: commit it (`git add -f default_config.json`) only if you want everyone who
can see the repository to have your relay's address.

Then in another terminal: `python3 main.py`. To check that an installation is healthy at any time:
`python3 main.py --self-test` (or `ANSxVault --self-test` for the packaged app).

**Size limit without cloud storage:** every piece travels inside the package, which becomes about 11 times the file's size, so files are limited to
**10 MB** until you add cloud storage in Settings.

---

## Part B — a real cloud bucket for shards (Cloudflare R2, free tier, ≈20 min)

Why: the point of 12 shards is that no single place holds them all. Use **two or three different providers** if you can.

1. Create a Cloudflare account → **R2 Object Storage**. (R2 asks for a payment method even for the free tier; the free tier is 10 GB and has no download fees.)
2. **Create bucket**: name `ansx-shards`. Leave it **private** (never make it public — presigned links handle access).
3. **R2 → Manage API Tokens → Create API token**:
   - Permission: **Object Read & Write**
   - Scope: **only** the `ansx-shards` bucket
   - Click create and **copy the Access Key ID and Secret Access Key now** (shown once) and the S3 endpoint `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
4. **Add it in the app** (no file editing): **Settings → Cloud storage → Add an account**. Choose *Cloudflare R2*, paste the
   Account ID, bucket name, Access Key ID and Secret Access Key, and press **Test connection**. You want four green ticks
   (upload works, download links work, bucket is private, cleaned up). Then **Save**.
   *(Advanced: the app stores this in `~/.ansx_vault/storage_targets.json` with owner-only permissions; you can also edit it by hand.)*
5. **Add a second provider** (recommended). AWS S3:
   - S3 → Create bucket (region near you), keep **Block all public access = ON**.
   - IAM → Users → Create user → attach an inline policy allowing only `s3:PutObject`, `s3:GetObject` and `s3:DeleteObject` on `arn:aws:s3:::YOUR-BUCKET/*` (delete lets the app clean up when you remove a file) → Security credentials → Create access key.
   - Add a second object to the JSON list: `{"name":"aws-eu","bucket":"YOUR-BUCKET","region":"eu-west-1","access_key":"...","secret_key":"..."}` (omit `endpoint_url`).
   Shard *i* goes to target *i mod number-of-targets*, so 2 targets = 6+5 shards each.
6. **Removing a file** from your vault can also delete its pieces from your cloud storage (the checkbox in the
   Remove dialog). Your local copy is only removed once every piece is gone; if a key cannot delete, the file stays
   listed and the app says which account refused. If someone has not picked the file up yet, the box starts unticked,
   because they need those pieces to open it.
7. **Try it**: **Protect** → drop a small file. The result should say *“11 pieces are in your cloud storage”*. Then look in the bucket dashboard: you should see objects named `<random>/s01.bin` … `s11.bin`.
   - `0 in cloud… no cloud storage configured` → the JSON file is missing/unreadable (check path and that it's valid JSON).
   - `uploads FAILED: AccessDenied` → wrong keys or the token isn't scoped to that bucket.
   - `SignatureDoesNotMatch` → wrong secret key or wrong `endpoint_url`/region.
7. **Do not add a lifecycle "auto-delete" rule** to buckets holding vaults you want to keep: deleting shards destroys the vault (you need any 8 of 12). Links inside a ghost map expire after 7 days but are re-issued automatically each time the owner sends the vault, as long as the credentials are on that machine.

---

### Part B, extra — Backblaze B2 (easiest, no credit card for the free 10 GB) and the checker

- **Backblaze B2**: sign up at backblaze.com → B2 Cloud Storage → *Create a Bucket* (globally unique name, **Private**, leave Object Lock off). On the bucket page note the **Endpoint** (e.g. `s3.us-west-004.backblazeb2.com`). *Application Keys → Add a New Application Key*: allow access to **only that bucket**, type **Read and Write**, create, and copy the **keyID** and **applicationKey** (shown once; never use the Master key). Config entry:
  `{"name":"b2-main","bucket":"YOUR-BUCKET","region":"us-west-004","endpoint_url":"https://s3.us-west-004.backblazeb2.com","access_key":"<keyID>","secret_key":"<applicationKey>"}` (use the region string from your own endpoint).
- **Always run the checker after editing the config:** `python check_storage.py`. Every target must print four ✔ lines. It uploads a test object, downloads it through a presigned link exactly like a receiver, and confirms the bucket is private.

---

## Part C — deploy the relay so people can reach each other (≈45 min, ≈$5/month)

Render's free tier has no persistent disk, so use a small VPS (Hetzner, DigitalOcean, Lightsail…) with Ubuntu.

1. **Create the VPS** and a domain/subdomain, e.g. `relay.example.com`, with an **A record** pointing at the server's IP.
2. **SSH in** and install Docker: `curl -fsSL https://get.docker.com | sh`
3. **Copy the project** to the server (`git clone` your repo, or `scp -r relay Dockerfile`… the build needs the `relay/` folder).
4. **Build and run** (note `127.0.0.1:` so the port is NOT public):
   ```bash
   docker build -f relay/Dockerfile -t ansx-relay .
   docker volume create ansx-data
   docker run -d --name ansx-relay --restart unless-stopped \
     -p 127.0.0.1:8000:8000 -v ansx-data:/data ansx-relay
   curl http://127.0.0.1:8000/          # must print {"node":"ANSX Relay",...}
   ```
   If the build fails, send me the error text; this Dockerfile has never been built.
5. **HTTPS with Caddy** (automatic certificates):
   ```bash
   sudo apt install -y caddy
   echo 'relay.example.com { reverse_proxy 127.0.0.1:8000 }' | sudo tee /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   sudo ufw allow 80,443/tcp && sudo ufw allow OpenSSH && sudo ufw enable
   ```
6. **Verify from your laptop**: `curl https://relay.example.com/`.
7. **Tell every client where the relay is**: easiest is **Settings → Connection**, paste the address, press **Test**, then **Save**. Or (pick one):
   - environment variable: `export ANSX_RELAY_URL=https://relay.example.com`
   - or file `~/.ansx_vault/config.json`: `{"relay_url": "https://relay.example.com"}`
8. **Backups**: `docker run --rm -v ansx-data:/data -v $PWD:/b alpine tar czf /b/ansx-data.tgz /data` (run daily via cron). Without it, a disk failure loses identities and in-flight transfers.
9. **Troubleshooting**
   - App shows `⚠ Relay unreachable` → wrong URL, DNS not yet propagated, or firewall.
   - `401 Timestamp outside the allowed window` → a computer's clock is off by more than 2 minutes; fix the clock.
   - `409 already registered` for your own name → someone registered that name first with a different key; choose another name.

---

## Part D — the real two-person test (≈30 min, needs two computers)

1. On **both** computers: complete Part A steps 1–3 and set the relay address (Settings → Connection, or Part C step 7).
   Only the person who **protects** files needs the cloud-storage account; receivers download through links inside the package.
2. **Create your identity** on each computer (first launch): choose a name (e.g. `alice` / `bob`), then *NFC card* or *Passphrase*.
3. **Alice → Protect**: drop a file on the drop zone. Wait for the green *“is protected”* message.
4. **Alice → Send**: press **Send** next to the file (or use the Send screen). Step 2, **Choose a person**: press **Refresh people** and pick Bob.
   (Bob must have opened the app once so the relay knows him.)
5. **Verify identities out of band** (once per pair). Open **People & keys** on both computers: your own fingerprint is at the top.
   Read it to each other over a phone call. Then select the other person and press **I compared it — mark as verified**
   (only if it matches exactly). On the review step, Alice can also press **I verified it**.
6. **Alice → Review and send → Send securely.** A progress bar runs, then *“Sent to bob”*.
7. **Bob → Inbox**: within ~8 seconds a notification appears and the file is listed with the sender's key status. Select it and press
   **Accept and open**. The file is saved to `Downloads/A.N.Sx Vault` and that folder opens. It must be identical to Alice's original.
8. **Alice → Inbox → Sent** shows **Delivered**.
9. **Break things on purpose**: unplug the network during an upload, reconnect, press **Resume sending**; have Bob press **Decline** and watch Alice's
   status change to *Declined*; press *Cancel this transfer* on a waiting transfer.

---

## Part E — NFC hardware (what you have now)

1. Arduino + PN532 wired over I²C (IRQ→D2, RESET→D3, as in `arduino_nfc/arduino_nfc.ino`).
2. Arduino IDE → install **Adafruit PN532** library → upload the sketch. **Close the Serial Monitor** afterwards (only one program can hold the port).
   **Already have a reader?** Upload the sketch again after updating the app: firmware **v2** is what locks cards and
   refuses a swapped card. The app still works with the old firmware, but logs *"The reader runs old firmware"*.
3. Use **MIFARE Classic 1K** cards. The app writes a 16-character secret to block 4 and locks that sector with a key made
   from the card's ID and this computer's protected secret. Cards set up before firmware v2 are locked automatically the
   next time you log in with them (keep the card on the reader until "Protecting your card…" finishes).
4. If the app says no reader: Windows → Device Manager shows a COM port; Linux → add yourself to `dialout` (`sudo usermod -aG dialout $USER`, re-login); macOS → `/dev/cu.usb*`.
5. What firmware v2 protects against:
   - **A card swapped** between the check and the write during setup: the write names the card it checked, and the
     reader refuses any other card.
   - **Reading the card with an ordinary reader or phone app**: they use the factory key and get nothing.
   - **The reader getting stuck**: every wait for a card has a time limit, and Cancel works.
6. **Known weakness**: Mifare Classic's own encryption is broken. Someone who gets hold of your card and has a
   specialist tool (e.g. a Proxmark) can still recover its key and copy it within minutes. NTAG 424 DNA cards fix this
   with a real challenge-response; when you get them, the firmware and `nfc_serial.py` need that rewrite.

---

## Part F — optional: the blockchain layer (skip unless you want it)

The relay already does the directory and the inbox. If you still want the on-chain registry: get Amoy test MATIC from https://faucet.polygon.technology/, `export ANSX_DEPLOYER_KEY=0x…`, run `python deploy_contracts.py` (it writes the addresses into `settings.py`). Note: the GUI no longer uses the chain for sending; only identity registration would.
