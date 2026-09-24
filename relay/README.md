# ANSX Relay

Directory + authenticated store-and-forward mailbox for A.N.Sx Vault. It only ever holds
**end-to-end-encrypted** blobs and public keys. See the module docstring in `server.py` for the protocol.

## Run locally
```bash
pip install -r relay/requirements.txt
uvicorn relay.server:create_app --factory --port 8000
# point the app at it (default is already http://127.0.0.1:8000):
export ANSX_RELAY_URL=http://127.0.0.1:8000
```

## Deploy
```bash
docker build -f relay/Dockerfile -t ansx-relay .
docker run -d -p 8000:8000 -v ansx-data:/data ansx-relay
```
Put it behind HTTPS (Caddy / nginx / your host's TLS), then set `ANSX_RELAY_URL=https://relay.example.com`
on every client (or `{"relay_url": "..."}` in `~/.ansx_vault/config.json`).

* **Single worker only** (rate limits are in-process). SQLite in WAL mode is plenty for thousands of users.
* **Back up `/data`** (or accept that in-flight transfers are lost; identities are re-registrable only by the key holder).
* Limits (env): `ANSX_MAX_TRANSFER_MB` (128), `ANSX_USER_QUOTA_MB` (512), `ANSX_TTL_HOURS` (168), `ANSX_CHUNK_KB` (256).

## API (all signed unless marked public)
| Call | Purpose |
|---|---|
| `GET /` , `GET /v1/limits` *(public)* | health, limits |
| `POST /v1/identity/register` | self-signed; names are immutable |
| `GET /v1/identity/resolve/{name}`, `GET /v1/identity/users` *(public)* | directory |
| `POST /v1/transfers` | announce (to, size, sha256) -> id |
| `PUT /v1/transfers/{id}/chunks/{n}` | idempotent, per-chunk SHA-256 |
| `POST /v1/transfers/{id}/complete` | server verifies whole-file hash |
| `GET /v1/inbox`, `GET /v1/outbox` | pending incoming / sent status |
| `GET /v1/transfers/{id}/download` | HTTP Range (resumable), recipient only |
| `POST /v1/transfers/{id}/ack` / `reject`, `DELETE /v1/transfers/{id}` | delivered / declined / cancelled (blob deleted) |

## What a malicious or compromised relay can and cannot do
Can: see who talks to whom, when, and message sizes; drop, delay or refuse messages; lie about who is registered.
Cannot: read contents; forge a message from a user; make a client accept a swapped key for a contact it has already pinned.
