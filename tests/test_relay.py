"""End-to-end tests: RelayClient against a REAL relay server (uvicorn on a local port)."""
import os
import socket
import threading
import time

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from relay.server import Config, create_app  # noqa: E402
from relay_client import Cancelled, RelayClient, RelayError, sha256_file  # noqa: E402


def _keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode(),
            k.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode())


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    cfg = Config(data_dir=str(tmp_path_factory.mktemp("relay")), chunk_size=4096, max_transfer_bytes=200_000,
                 user_quota_bytes=300_000, register_per_ip_hour=1000, transfers_per_user_hour=1000, clock_skew=60)
    app = create_app(cfg)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/", timeout=0.2); break
        except requests.RequestException:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app, cfg
    srv.should_exit = True


@pytest.fixture(scope="module")
def users(server):
    url = server[0]
    out = {}
    for name in ("alice", "bob", "carol"):
        priv, pub = _keypair()
        c = RelayClient(url, name, priv, retries=1)
        c.register(pub)
        out[name] = (c, priv, pub)
    return out


def _file(tmp_path, size, name="f.bin"):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return str(p)


def test_registration_needs_proof_of_possession_and_names_are_immutable(server, users):
    url = server[0]
    priv_e, pub_e = _keypair()
    _, _, pub_alice = users["alice"]
    # Mallory cannot register "alice" again (409) ...
    with pytest.raises(RelayError) as e:
        RelayClient(url, "alice", priv_e, retries=0).register(pub_e)
    assert e.value.status == 409
    # ... nor register a name using someone else's public key (signature won't verify).
    with pytest.raises(RelayError) as e:
        RelayClient(url, "mallory", priv_e, retries=0).register(pub_alice)
    assert e.value.status == 401
    # ... nor a bad name / weak key
    with pytest.raises(RelayError):
        RelayClient(url, "../x", priv_e, retries=0).register(pub_e)
    assert "alice" in RelayClient(url).users()
    r = RelayClient(url).resolve("bob")
    assert r["public_key"] == users["bob"][2] and len(r["fingerprint"].split()) == 8


def test_auth_rejects_forgery_replay_and_stale_requests(server, users):
    url = server[0]
    alice, _, _ = users["alice"]
    # forged: eve signs as alice with her own key
    priv_e, _ = _keypair()
    with pytest.raises(RelayError) as e:
        RelayClient(url, "alice", priv_e, retries=0).inbox()
    assert e.value.status == 401
    # replay: the exact same signed request twice
    h = alice._sign_headers("GET", "/v1/inbox", b"")
    assert requests.get(url + "/v1/inbox", headers=h).status_code == 200
    assert requests.get(url + "/v1/inbox", headers=h).status_code == 401
    # stale timestamp
    h = alice._sign_headers("GET", "/v1/inbox", b"")
    h["X-ANSX-Ts"] = str(int(time.time()) - 3600)
    assert requests.get(url + "/v1/inbox", headers=h).status_code == 401
    # signature is bound to the path: reuse on another endpoint fails
    h = alice._sign_headers("GET", "/v1/inbox", b"")
    assert requests.get(url + "/v1/outbox", headers=h).status_code == 401
    # no auth at all
    assert requests.get(url + "/v1/inbox").status_code == 401


def test_full_transfer_roundtrip_and_ack_deletes_blob(server, users, tmp_path):
    url, app, cfg = server
    alice, bob = users["alice"][0], users["bob"][0]
    src = _file(tmp_path, 50_001)            # 13 chunks of 4096, last one partial
    progress = []
    tid = alice.send_file(src, "bob", progress=lambda d, t: progress.append((d, t)))
    assert progress[-1] == (50_001, 50_001)

    items = bob.inbox()
    assert [i["id"] for i in items] == [tid] and items[0]["from"] == "alice"
    assert items[0]["sender_fingerprint"] == RelayClient(url).resolve("alice")["fingerprint"]
    assert alice.outbox()[0]["state"] == "ready"

    dest = str(tmp_path / "got.bin")
    bob.download(items[0], dest)
    assert sha256_file(dest) == sha256_file(src)
    assert bob.inbox() == []
    assert alice.outbox()[0]["state"] == "delivered"          # sender can see it was picked up
    assert not [f for f in os.listdir(os.path.join(cfg.data_dir, "blobs")) if f.startswith(tid)]
    with pytest.raises(RelayError) as e:                       # cannot be downloaded twice
        bob.download(items[0], str(tmp_path / "again.bin"))
    assert e.value.status == 409


def test_only_the_recipient_can_read_and_only_the_sender_can_write(server, users, tmp_path):
    alice, bob, carol = users["alice"][0], users["bob"][0], users["carol"][0]
    src = _file(tmp_path, 9000)
    tid = alice.send_file(src, "bob")
    item = bob.inbox()[0]
    with pytest.raises(RelayError) as e:
        carol.download(item, str(tmp_path / "steal.bin"))
    assert e.value.status == 403
    for fn in (lambda: carol.status(tid), lambda: carol.reject(tid), lambda: carol.cancel_transfer(tid)):
        with pytest.raises(RelayError) as e:
            fn()
        assert e.value.status == 403
    assert carol.inbox() == []
    bob.reject(tid)                                            # consent: recipient can decline
    assert bob.inbox() == [] and alice.outbox()[0]["state"] == "rejected"


def test_upload_resumes_after_interruption(server, users, tmp_path):
    alice, bob = users["alice"][0], users["bob"][0]
    src = _file(tmp_path, 40_000)
    calls = {"n": 0}
    orig = alice._request

    def flaky(method, target, **kw):
        if method == "PUT":
            calls["n"] += 1
            if calls["n"] == 5:
                raise RelayError(400, "simulated hard failure")      # non-retryable: aborts the send
        return orig(method, target, **kw)

    alice._request = flaky
    with pytest.raises(RelayError):
        alice.send_file(src, "bob")
    alice._request = orig
    tid = alice.last_transfer_id
    assert len(alice.status(tid)["received"]) == 4

    puts = []
    alice._request = lambda m, t, **kw: (puts.append(t) if m == "PUT" else None, orig(m, t, **kw))[1]
    assert alice.send_file(src, "bob", resume_id=tid) == tid
    alice._request = orig
    assert len(puts) == 10 - 4                                       # only the missing chunks were sent
    dest = str(tmp_path / "r.bin")
    bob.download(bob.inbox()[0], dest)
    assert sha256_file(dest) == sha256_file(src)
    with pytest.raises(RelayError):                                   # resume must match the same file
        alice.send_file(_file(tmp_path, 100, "other.bin"), "bob", resume_id=tid)


def test_download_resumes_from_partial_file(server, users, tmp_path):
    alice, bob = users["alice"][0], users["bob"][0]
    src = _file(tmp_path, 30_000)
    alice.send_file(src, "bob")
    item = bob.inbox()[0]
    dest = str(tmp_path / "d.bin")
    with open(src, "rb") as f:                                        # pretend 10_000 bytes were already fetched
        open(dest + ".part", "wb").write(f.read(10_000))
    seen = []
    bob.download(item, dest, progress=lambda d, t: seen.append(d))
    assert sha256_file(dest) == sha256_file(src) and seen[0] > 10_000


def test_corruption_is_caught(server, users, tmp_path):
    url, app, cfg = server
    alice, bob = users["alice"][0], users["bob"][0]
    # chunk corrupted in transit -> relay refuses it (hash header)
    src = _file(tmp_path, 8192)
    created = alice._json("POST", "/v1/transfers", {"to": "bob", "size": 8192, "sha256": sha256_file(src)})
    tid = created["id"]
    r = requests.put(url + f"/v1/transfers/{tid}/chunks/0", data=b"x" * 4096,
                     headers={**alice._sign_headers("PUT", f"/v1/transfers/{tid}/chunks/0", b"x" * 4096),
                              "X-Chunk-SHA256": "00" * 32})
    assert r.status_code == 422
    # wrong chunk length
    r = requests.put(url + f"/v1/transfers/{tid}/chunks/0", data=b"x" * 10,
                     headers=alice._sign_headers("PUT", f"/v1/transfers/{tid}/chunks/0", b"x" * 10))
    assert r.status_code == 400
    # completing early names the missing chunks
    with pytest.raises(RelayError) as e:
        alice._json("POST", f"/v1/transfers/{tid}/complete", {})
    assert e.value.status == 409 and "Missing" in e.value.detail
    # whole-file hash mismatch (sender committed to a different digest) -> discarded, must resend
    for i in range(2):
        chunk = os.urandom(4096)
        alice._request("PUT", f"/v1/transfers/{tid}/chunks/{i}", body=chunk)
    with pytest.raises(RelayError) as e:
        alice._json("POST", f"/v1/transfers/{tid}/complete", {})
    assert e.value.status == 422
    assert alice.status(tid)["received"] == []
    alice.cancel_transfer(tid)
    # download whose bytes differ from the committed hash is discarded, nothing is acked
    src2 = _file(tmp_path, 5000)
    alice.send_file(src2, "bob")
    item = bob.inbox()[0]
    item = dict(item, sha256="00" * 32)                                # tampered metadata on the receiver side
    with pytest.raises(RelayError) as e:
        bob.download(item, str(tmp_path / "bad.bin"))
    assert e.value.status == 422 and not os.path.exists(str(tmp_path / "bad.bin"))
    assert len(bob.inbox()) == 1                                       # still pending: not acknowledged
    bob.reject(item["id"])


def test_limits_quota_and_unknown_recipient(server, users, tmp_path):
    alice = users["alice"][0]
    with pytest.raises(RelayError) as e:
        alice.send_file(_file(tmp_path, 10), "nobody")
    assert e.value.status == 404
    with pytest.raises(RelayError) as e:
        alice.send_file(_file(tmp_path, 250_000), "bob")               # > max_transfer_bytes (200k)
    assert e.value.status == 413
    tids = []
    try:
        for _ in range(3):
            tids.append(alice._json("POST", "/v1/transfers", {"to": "bob", "size": 150_000, "sha256": "0" * 64})["id"])
        pytest.fail("quota not enforced")
    except RelayError as e:
        assert e.status == 413 and len(tids) == 2                       # 2*150k=300k fits, 3rd does not
    finally:
        for t in tids:
            alice.cancel_transfer(t)


def test_cancel_and_expiry_cleanup(server, users, tmp_path):
    url, app, cfg = server
    alice, bob = users["alice"][0], users["bob"][0]
    tid = alice.send_file(_file(tmp_path, 6000), "bob")
    alice.cancel_transfer(tid)
    assert bob.inbox() == []
    # expiry: force the deadline into the past, cleanup must delete the blob and mark it expired
    tid2 = alice.send_file(_file(tmp_path, 6000), "bob")
    import sqlite3
    conn = sqlite3.connect(os.path.join(cfg.data_dir, "relay.db"))
    conn.execute("UPDATE transfers SET expires=? WHERE id=?", (int(time.time()) - 5, tid2)); conn.commit(); conn.close()
    assert bob.inbox() == []                                           # expired items are hidden immediately
    assert app.state.cleanup()["expired"] >= 1
    assert not [f for f in os.listdir(os.path.join(cfg.data_dir, "blobs")) if f.startswith(tid2)]
    assert [o for o in alice.outbox() if o["id"] == tid2][0]["state"] == "expired"


def test_cancel_callback_stops_send(server, users, tmp_path):
    alice = users["alice"][0]
    src = _file(tmp_path, 30_000)
    n = {"c": 0}
    def cancel():
        n["c"] += 1
        return n["c"] > 6
    with pytest.raises(Cancelled):
        alice.send_file(src, "bob", cancel=cancel)
    alice.cancel_transfer(alice.last_transfer_id)


def test_network_errors_are_retried(server, users, tmp_path, monkeypatch):
    bob = users["bob"][0]
    src = _file(tmp_path, 9000)
    real = requests.Session.request
    state = {"fail": 3}
    def flaky(self, method, url, **kw):
        if method == "PUT" and state["fail"] > 0:
            state["fail"] -= 1
            raise requests.ConnectionError("boom")
        return real(self, method, url, **kw)
    monkeypatch.setattr(requests.Session, "request", flaky)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = RelayClient(server[0], "alice", users["alice"][1], retries=4)
    tid = c.send_file(src, "bob")
    assert state["fail"] == 0
    monkeypatch.undo()
    bob.reject(tid)


def test_a_new_relay_is_in_wal_mode_before_its_first_request(tmp_path):
    """
    Regression: every request used to switch the database to WAL itself, so the first requests to a new relay raced
    to do it and SQLite failed one of them at once ("database is locked", a 500). The switch now happens at start.
    """
    import sqlite3
    create_app(Config(data_dir=str(tmp_path)))
    conn = sqlite3.connect(str(tmp_path / "relay.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
