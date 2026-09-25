"""The three user actions (protect / send-wrap / restore) with no GUI: correctness, errors, hygiene."""
import hashlib
import os

import pytest

import courier
import engine
import paths
import vault_service as vs
from cloud_dispatcher import CloudDispatcher
from security_core import SecurityCore, VaultLedger

pytestmark = pytest.mark.skipif(not engine.available(), reason="native engine not built")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))


@pytest.fixture(scope="module")
def people():
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    out = {}
    for name, auth in (("vs_owner", "nfc"), ("vs_friend", "passphrase")):
        SecurityCore.establish_identity(name, "seed-for-" + name + "-xxxx", auth=auth)
        out[name] = SecurityCore.load_identity_for_user(name)
    return out


class FakeS3:
    store = {}

    def __init__(self, target): pass

    def upload_file(self, path, bucket, key, Callback=None):
        FakeS3.store[(bucket, key)] = open(path, "rb").read()
        if Callback:
            Callback(len(FakeS3.store[(bucket, key)]))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://{Params['Bucket']}.example/{Params['Key']}?sig=x"

    def delete_object(self, Bucket, Key):
        FakeS3.store.pop((Bucket, Key), None)


@pytest.fixture
def fake_cloud(monkeypatch):
    FakeS3.store = {}

    def dl(url, dest, expected_sha):
        key = url.split(".example/")[1].split("?")[0]
        data = next((v for (b, k), v in FakeS3.store.items() if k == key), None)
        if data is None or (expected_sha and hashlib.sha256(data).hexdigest() != expected_sha):
            return False
        open(dest, "wb").write(data)
        return True

    monkeypatch.setattr(courier, "_download", dl)


def _src(tmp_path, size=70_000, name="report.pdf"):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return str(p)


def test_passphrase_and_card_identities_report_their_mode(people):
    assert SecurityCore.auth_mode("vs_owner") == "nfc"
    assert SecurityCore.auth_mode("vs_friend") == "passphrase"
    assert SecurityCore.auth_mode("nobody") == "nfc"
    with pytest.raises(Exception):
        SecurityCore.establish_identity("short_pw", "tooshort", auth="passphrase")


def test_protect_then_restore_inline(people, tmp_path):
    owner = people["vs_owner"]
    src = _src(tmp_path)
    steps = []
    res = vs.protect(src, owner, progress=lambda label, pct: steps.append(pct),
                     dispatcher=CloudDispatcher(targets=[]))
    assert steps[0] < steps[-1] == 100 and steps == sorted(steps)          # progress only moves forward
    assert (res.cloud, res.inline, res.storage_configured) == (0, 12, False)
    assert res.entry["size"] == 70_000 and res.entry["storage"] == {"cloud": 0, "inline": 12}
    assert VaultLedger.get(res.entry["id"])["original_filename"] == "report.pdf"
    assert not os.listdir(paths.tmp_dir())                                  # no plaintext-derived leftovers

    out = tmp_path / "back.pdf"
    info = vs.reconstruct(res.entry["ghost_map_path"], owner["private_key"] if "private_key" in owner else
                          SecurityCore.load_identity_for_user("vs_owner")["private_key"], str(out),
                          dispatcher=CloudDispatcher(targets=[]))
    assert out.read_bytes() == open(src, "rb").read() and info["original_file"] == "report.pdf"
    assert not os.listdir(paths.tmp_dir())


def _owner_with_keys():
    SecurityCore.verify_login("vs_owner", "seed-for-vs_owner-xxxx")
    return SecurityCore.load_identity_for_user("vs_owner")


def test_cloud_storage_splits_pieces_and_still_restores(people, tmp_path, fake_cloud):
    owner = _owner_with_keys()
    targets = [{"name": "a", "bucket": "b1", "access_key": "k", "secret_key": "s"},
               {"name": "b", "bucket": "b2", "access_key": "k", "secret_key": "s"}]
    disp = CloudDispatcher(targets=targets, client_factory=FakeS3)
    src = _src(tmp_path, 40_000)
    res = vs.protect(src, owner, dispatcher=disp)
    assert (res.cloud, res.inline) == (11, 1) and {b for b, _ in FakeS3.store} == {"b1", "b2"}
    out = tmp_path / "o.bin"
    vs.reconstruct(res.entry["ghost_map_path"], owner["private_key"], str(out), dispatcher=disp)
    assert out.read_bytes() == open(src, "rb").read()


def test_partial_cloud_failure_falls_back_inline_and_reports_it(people, tmp_path, fake_cloud):
    owner = _owner_with_keys()

    class Flaky(FakeS3):
        def upload_file(self, path, bucket, key, Callback=None):
            if "s03" in key or "s07" in key:
                raise RuntimeError("AccessDenied")
            super().upload_file(path, bucket, key, Callback)

    disp = CloudDispatcher(targets=[{"name": "a", "bucket": "b1", "access_key": "k", "secret_key": "s"}],
                           client_factory=Flaky)
    res = vs.protect(_src(tmp_path, 20_000), owner, dispatcher=disp)
    assert (res.cloud, res.inline) == (9, 3) and len(res.upload_errors) == 2
    out = tmp_path / "o.bin"
    vs.reconstruct(res.entry["ghost_map_path"], owner["private_key"], str(out), dispatcher=disp)
    assert out.exists()


def test_rewrap_only_the_receiver_can_open_it(people, tmp_path):
    owner, friend = _owner_with_keys(), people["vs_friend"]
    SecurityCore.verify_login("vs_friend", "seed-for-vs_friend-xxxx")
    friend = SecurityCore.load_identity_for_user("vs_friend")
    src = _src(tmp_path, 30_000)
    res = vs.protect(src, owner, dispatcher=CloudDispatcher(targets=[]))
    sent = vs.rewrap_for(res.entry, owner, friend["public_key"], dispatcher=CloudDispatcher(targets=[]))

    with pytest.raises(vs.VaultError):                                    # the sender cannot reopen what they sent
        vs.open_package(sent, owner["private_key"])
    out = tmp_path / "friend.bin"
    vs.reconstruct(sent, friend["private_key"], str(out), dispatcher=CloudDispatcher(targets=[]))
    assert out.read_bytes() == open(src, "rb").read()
    assert not [f for f in os.listdir(paths.outbox_dir()) if f.startswith("carrier_")]   # temp carrier removed


def test_friendly_errors(people, tmp_path):
    owner = _owner_with_keys()
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    for bad, msg in ((str(empty), "empty"), (str(tmp_path / "nope.bin"), "no longer exists")):
        with pytest.raises(vs.VaultError, match=msg):
            vs.protect(bad, owner, dispatcher=CloudDispatcher(targets=[]))
    with pytest.raises(vs.VaultError, match="locked"):
        vs.protect(_src(tmp_path, 100), {"operator": "x"}, dispatcher=CloudDispatcher(targets=[]))
    with pytest.raises(vs.VaultError, match="locked"):
        vs.open_package("whatever.png", "")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image")
    with pytest.raises(vs.VaultError):
        vs.open_package(str(junk), owner["private_key"])
    with pytest.raises(vs.Cancelled):
        vs.protect(_src(tmp_path, 100), owner, dispatcher=CloudDispatcher(targets=[]), cancel=lambda: True)
    assert not os.listdir(paths.tmp_dir())                                # even failures leave nothing behind


def test_ledger_ids_and_removal(people, tmp_path):
    owner = _owner_with_keys()
    res = vs.protect(_src(tmp_path, 500), owner, dispatcher=CloudDispatcher(targets=[]))
    assert VaultLedger.get(res.entry["id"]) and os.path.exists(res.entry["ghost_map_path"])
    assert VaultLedger.remove_entry(res.entry["id"]) is True
    assert VaultLedger.get(res.entry["id"]) is None and not os.path.exists(res.entry["ghost_map_path"])
    assert VaultLedger.remove_entry("nonexistent") is False


def test_activity_log(tmp_path):
    import activity
    activity.add("sent", "Sent report.pdf", "to bob", operator="vs_owner")
    activity.add("bogus-kind", "x")
    items = activity.recent(5)
    assert items[0]["kind"] == "security" and items[1]["title"] == "Sent report.pdf"
    assert activity.recent(5, operator="someone_else")[0]["title"] == "x"   # untagged items are shared


def test_vault_lists_are_private_per_operator(people, tmp_path):
    owner = _owner_with_keys()
    res = vs.protect(_src(tmp_path, 400, "mine.txt"), owner, dispatcher=CloudDispatcher(targets=[]))
    assert res.entry["owner"] == "vs_owner"
    names_owner = [e["original_filename"] for e in VaultLedger.load(owner="vs_owner")]
    names_friend = [e["original_filename"] for e in VaultLedger.load(owner="vs_friend")]
    assert "mine.txt" in names_owner and "mine.txt" not in names_friend      # a housemate never sees my file names
    legacy = VaultLedger.add_entry("old.bin", "/x/old.png", "2025-01-01 00:00:00")           # pre-owner entries
    assert legacy["owner"] is None
    assert "old.bin" in [e["original_filename"] for e in VaultLedger.load(owner="vs_friend")]


def test_relay_address_priority(tmp_path, monkeypatch):
    """settings this session > env var > user's saved choice > address shipped with the app > local default"""
    import json
    import relay_config
    monkeypatch.setattr(relay_config, "_override", None)
    monkeypatch.delenv("ANSX_RELAY_URL", raising=False)
    monkeypatch.setattr(relay_config, "load_config", lambda: {})
    monkeypatch.setattr(relay_config, "bundled_default", lambda: "")
    assert relay_config.get_relay_url() == relay_config.DEFAULT_RELAY

    shipped = tmp_path / "default_config.json"
    shipped.write_text(json.dumps({"relay_url": "https://relay.example.com/"}))
    monkeypatch.undo()
    monkeypatch.setattr(relay_config, "_override", None)
    monkeypatch.delenv("ANSX_RELAY_URL", raising=False)
    monkeypatch.setattr(relay_config.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(relay_config, "load_config", lambda: {})
    assert relay_config.bundled_default() == "https://relay.example.com"          # trailing slash normalised
    assert relay_config.get_relay_url() == "https://relay.example.com"            # a fresh install already knows the relay

    monkeypatch.setattr(relay_config, "load_config", lambda: {"relay_url": "https://mine.example.org"})
    assert relay_config.get_relay_url() == "https://mine.example.org"             # the user's own choice beats the shipped one
    monkeypatch.setenv("ANSX_RELAY_URL", "https://env.example.net")
    assert relay_config.get_relay_url() == "https://env.example.net"

    shipped.write_text("{not json")                                               # a broken file must never crash the app
    monkeypatch.delenv("ANSX_RELAY_URL")
    monkeypatch.setattr(relay_config, "load_config", lambda: {})
    assert relay_config.bundled_default() == "" and relay_config.get_relay_url() == relay_config.DEFAULT_RELAY


def test_big_file_without_cloud_storage_is_refused_up_front(people, tmp_path):
    owner = _owner_with_keys()
    big = tmp_path / "video.mov"
    with open(big, "wb") as f:
        f.truncate(vs.INLINE_FILE_LIMIT + 1)                       # sparse file: no need to write 10 MB
    with pytest.raises(vs.VaultError, match="Add a cloud storage account"):
        vs.protect(str(big), owner, dispatcher=CloudDispatcher(targets=[]))
    assert not os.listdir(paths.tmp_dir())                          # refused before any work or temp files
    assert "protect_" not in " ".join(os.listdir(paths.tmp_dir()))


def _two_buckets():
    return [{"name": "a", "bucket": "b1", "access_key": "k", "secret_key": "s"},
            {"name": "b", "bucket": "b2", "access_key": "k", "secret_key": "s"}]


def test_deleting_a_files_cloud_pieces_removes_every_one(people, tmp_path, fake_cloud):
    owner = _owner_with_keys()
    disp = CloudDispatcher(targets=_two_buckets(), client_factory=FakeS3)
    res = vs.protect(_src(tmp_path, 40_000), owner, dispatcher=disp)
    refs = vs.cloud_pieces(res.entry, owner["private_key"])
    assert len(refs) == 11 and {(r["target"], r["key"]) for r in refs}          # read from the sealed package
    assert len(FakeS3.store) == 11
    out = vs.delete_cloud_pieces(res.entry, owner["private_key"], dispatcher=disp)
    assert out == {"total": 11, "deleted": 11, "problems": []} and FakeS3.store == {}
    assert os.path.exists(res.entry["ghost_map_path"])                          # the local package is not touched


def test_pieces_that_cannot_be_deleted_are_reported_not_ignored(people, tmp_path, fake_cloud):
    owner = _owner_with_keys()
    disp = CloudDispatcher(targets=_two_buckets(), client_factory=FakeS3)
    res = vs.protect(_src(tmp_path, 40_000), owner, dispatcher=disp)

    class Refuses(FakeS3):
        def delete_object(self, Bucket, Key):
            if Bucket == "b2":
                raise PermissionError("AccessDenied")
            super().delete_object(Bucket, Key)
    only_a = CloudDispatcher(targets=[_two_buckets()[0]], client_factory=FakeS3)    # account "b" removed from Settings
    out = vs.delete_cloud_pieces(res.entry, owner["private_key"], dispatcher=only_a)
    assert out["deleted"] < out["total"] and any("no longer set up" in p for p in out["problems"])
    out = vs.delete_cloud_pieces(res.entry, owner["private_key"],
                                 dispatcher=CloudDispatcher(targets=_two_buckets(), client_factory=Refuses))
    assert out["problems"] and all("refused" in p for p in out["problems"])


def test_a_file_kept_entirely_in_its_package_has_nothing_in_the_cloud(people, tmp_path):
    owner = _owner_with_keys()
    res = vs.protect(_src(tmp_path, 20_000), owner, dispatcher=CloudDispatcher(targets=[]))
    assert vs.delete_cloud_pieces(res.entry, owner["private_key"], dispatcher=CloudDispatcher(targets=[])) == \
        {"total": 0, "deleted": 0, "problems": []}
