"""End-to-end: vault -> self-wrapped ghost map -> re-wrap for receiver -> retrieve (cloud+inline) -> reconstruct."""
import base64
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import cloud_dispatcher
import courier
import engine
from ghost_map import GhostMap, GhostMapError

pytestmark = pytest.mark.skipif(not engine.available(), reason="native engine not built")


def _keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode(),
            k.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode())


class FakeS3:
    store = {}

    def __init__(self, target): pass

    def upload_file(self, path, bucket, key, Callback=None):
        data = open(path, "rb").read()
        FakeS3.store[(bucket, key)] = data
        if Callback:
            Callback(len(data))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://{Params['Bucket']}.example/{Params['Key']}?sig=x"


@pytest.fixture
def fake_cloud(monkeypatch):
    FakeS3.store = {}

    def dl(url, dest, expected_sha):
        import hashlib
        key = url.split(".example/")[1].split("?")[0]
        data = next((v for (b, k), v in FakeS3.store.items() if k == key), None)
        if data is None or (expected_sha and hashlib.sha256(data).hexdigest() != expected_sha):
            return False
        open(dest, "wb").write(data)
        return True

    monkeypatch.setattr(courier, "_download", dl)


TARGETS = [{"name": "t1", "bucket": "b1", "access_key": "a", "secret_key": "s"},
           {"name": "t2", "bucket": "b2", "access_key": "a", "secret_key": "s"}]


def _vault(tmp_path, data):
    src = tmp_path / "secret.bin"
    src.write_bytes(data)
    shards = tmp_path / "shards"
    key = os.urandom(32).hex()
    engine.shatter(str(src), key, out_dir=str(shards))
    return src, shards, key


def test_full_flow_with_cloud_and_inline_shards(tmp_path, fake_cloud):
    data = os.urandom(20000)
    src, shards, key = _vault(tmp_path, data)

    d = cloud_dispatcher.CloudDispatcher(targets=TARGETS, client_factory=FakeS3)
    for i in range(11):
        assert d.upload_shard(i, str(shards / f"fragment_{i + 1}.ansx"), lambda *_: None)
    assert {r["target"] for r in d.refs.values()} == {"t1", "t2"}          # spread across both targets

    manifest = courier.build_manifest(str(src), key, str(shards), d.uploaded_urls, d.refs)
    assert list(manifest["shard_payload"]) == ["fragment_12.ansx"]        # only shard 12 travels inline

    owner_priv, owner_pub = _keypair()
    recv_priv, recv_pub = _keypair()
    work = tmp_path / "w"
    work.mkdir()

    # Vault stage: wrapped to the OWNER's key. The raw key must not be recoverable without it.
    GhostMap.make_carrier(str(work / "c.png"), 300000)
    GhostMap.hide_payload_in_image(json.dumps(manifest), owner_pub, str(work / "c.png"), str(work / "vault.png"))
    with pytest.raises(GhostMapError):
        GhostMap.extract_payload_from_image("", str(work / "vault.png"))
    with pytest.raises(GhostMapError):
        GhostMap.extract_payload_from_image(recv_priv, str(work / "vault.png"))   # wrong identity

    # Send stage: owner unwraps, refreshes URLs, re-wraps for the receiver only.
    m2 = json.loads(GhostMap.extract_payload_from_image(owner_priv, str(work / "vault.png")))
    m2 = courier.refresh_manifest_urls(m2, cloud_dispatcher.CloudDispatcher(targets=TARGETS, client_factory=FakeS3))
    GhostMap.hide_payload_in_image(json.dumps(m2), recv_pub, str(work / "c.png"), str(work / "send.png"))
    with pytest.raises(GhostMapError):
        GhostMap.extract_payload_from_image(owner_priv, str(work / "send.png"))    # sender can't reopen it

    # Receive stage.
    m3 = json.loads(GhostMap.extract_payload_from_image(recv_priv, str(work / "send.png")))
    assert courier.fetch_shards(m3, str(work / "dl")) == 12
    out = work / "out.bin"
    engine.unshatter(str(work / "dl"), str(out), m3["ephemeral_key"])
    assert out.read_bytes() == data


def test_no_storage_configured_means_inline_not_fake_urls(tmp_path):
    src, shards, key = _vault(tmp_path, b"hello" * 1000)
    d = cloud_dispatcher.CloudDispatcher(targets=[])
    assert not d.configured
    assert d.upload_shard(0, str(shards / "fragment_1.ansx"), lambda *_: None) is None
    assert d.uploaded_urls == {}
    m = courier.build_manifest(str(src), key, str(shards), d.uploaded_urls, d.refs)
    assert len(m["shard_payload"]) == 12 and m["cloud_urls"] == {}
    assert courier.fetch_shards(m, str(tmp_path / "dl")) == 12


def test_failed_upload_is_reported_and_falls_back_inline(tmp_path):
    class Boom(FakeS3):
        def upload_file(self, *a, **k):
            raise RuntimeError("AccessDenied")

    src, shards, key = _vault(tmp_path, b"x" * 5000)
    d = cloud_dispatcher.CloudDispatcher(targets=TARGETS, client_factory=Boom)
    assert d.upload_shard(0, str(shards / "fragment_1.ansx"), lambda *_: None) is None
    assert "AccessDenied" in d.errors[0] and d.uploaded_urls == {}
    assert "fragment_1.ansx" in courier.build_manifest(str(src), key, str(shards), {}, {})["shard_payload"]


def test_tampered_or_malicious_shards_are_rejected(tmp_path, fake_cloud):
    src, shards, key = _vault(tmp_path, os.urandom(3000))
    m = courier.build_manifest(str(src), key, str(shards), {}, {})

    evil = json.loads(json.dumps(m))
    evil["shard_payload"]["../../evil.txt"] = base64.b64encode(b"x").decode()
    with pytest.raises(courier.CourierError):
        courier.fetch_shards(evil, str(tmp_path / "d1"))
    assert not (tmp_path / "evil.txt").exists()

    bad = json.loads(json.dumps(m))
    for i in range(1, 6):     # corrupt 5 shards -> hash check drops them -> only 7 valid < 8
        raw = bytearray(base64.b64decode(bad["shard_payload"][f"fragment_{i}.ansx"]))
        raw[-1] ^= 1
        bad["shard_payload"][f"fragment_{i}.ansx"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(courier.CourierError):
        courier.fetch_shards(bad, str(tmp_path / "d2"))

    one_bad = json.loads(json.dumps(m))   # a single corrupt shard is simply skipped; data still recovers
    raw = bytearray(base64.b64decode(one_bad["shard_payload"]["fragment_3.ansx"]))
    raw[-1] ^= 1
    one_bad["shard_payload"]["fragment_3.ansx"] = base64.b64encode(bytes(raw)).decode()
    assert courier.fetch_shards(one_bad, str(tmp_path / "d3")) == 11
    engine.unshatter(str(tmp_path / "d3"), str(tmp_path / "o"), key)
    assert (tmp_path / "o").read_bytes() == src.read_bytes()


def test_non_https_cloud_url_is_refused(tmp_path):
    assert courier._download("http://evil.example/x", str(tmp_path / "x"), None) is False
    assert courier._download("ftp://evil.example/x", str(tmp_path / "x"), None) is False
