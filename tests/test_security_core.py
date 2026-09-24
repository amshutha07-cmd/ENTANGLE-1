import json
import os
import sys

import pytest

import security_core
from security_core import SecurityCore, IdentityError, safe_name, public_key_fingerprint


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(SecurityCore, "get_geolocation", staticmethod(lambda: "12.3,45.6"))
    SecurityCore.lock()
    yield
    SecurityCore.lock()


@pytest.fixture(scope="module")
def alice():
    SecurityCore.initialize_system()
    SecurityCore._publish_identity = classmethod(lambda cls, *a, **k: None)
    SecurityCore.establish_identity("alice_t", "SEEDSEEDSEED1234")
    return "alice_t"


def _file(name):
    return os.path.join(security_core.IDENTITY_DIR, f"{name}.json")


def test_private_keys_are_not_on_disk_in_plaintext(alice):
    raw = open(_file(alice)).read()
    assert "BEGIN PRIVATE KEY" not in raw and "BEGIN RSA PRIVATE" not in raw
    data = json.loads(raw)
    assert "private_key" not in data and "eth_private_key" not in data and "hardware_anchor" not in data
    if sys.platform != "win32":   # Windows has no POSIX modes; the keys are encrypted regardless
        assert oct(os.stat(_file(alice)).st_mode & 0o777) == "0o600"


def test_login_gates_private_keys(alice):
    SecurityCore.lock()
    assert "private_key" not in SecurityCore.load_identity_for_user(alice)
    assert not SecurityCore.verify_login(alice, "WRONGSEEDWRONG12")
    assert "private_key" not in SecurityCore.load_identity_for_user(alice)
    assert SecurityCore.verify_login(alice, "SEEDSEEDSEED1234")
    ident = SecurityCore.load_identity_for_user(alice)
    assert ident["private_key"].startswith("-----BEGIN PRIVATE KEY")
    SecurityCore.lock(alice)
    assert "private_key" not in SecurityCore.load_identity_for_user(alice)


def test_tampered_identity_file_fails_authentication(alice):
    p = _file(alice)
    orig = open(p).read()
    try:
        d = json.loads(orig)
        d["wrapped"]["ct"] = d["wrapped"]["ct"][:-2] + ("00" if d["wrapped"]["ct"][-2:] != "00" else "01")
        json.dump(d, open(p, "w"))
        assert not SecurityCore.verify_login(alice, "SEEDSEEDSEED1234")
    finally:
        open(p, "w").write(orig)


def test_no_geolocation_dependency(alice, monkeypatch):
    monkeypatch.setattr(SecurityCore, "get_geolocation", staticmethod(lambda: "99.9,99.9"))
    assert SecurityCore.verify_login(alice, "SEEDSEEDSEED1234")


def test_duplicate_and_path_traversal_rejected(alice):
    with pytest.raises(IdentityError):
        SecurityCore.establish_identity(alice, "SEEDSEEDSEED1234")
    for bad in ("../evil", "a/b", "", "..", ".hidden", "x" * 40):
        with pytest.raises(IdentityError):
            safe_name(bad)
    assert SecurityCore.load_identity_for_user("../etc/passwd") is None
    assert SecurityCore.delete_identity("../x") is False


def test_legacy_identity_migrates(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    seed = "LEGACYSEED123456"
    legacy = {"operator": "old_t", "hardware_anchor": SecurityCore._legacy_anchor(seed, "12.3,45.6"),
              "public_key": pub, "private_key": priv, "machine_id": "1", "vault_id": "ANSX-OLD",
              "eth_address": "", "eth_private_key": ""}
    json.dump(legacy, open(_file("old_t"), "w"))
    assert not SecurityCore.verify_login("old_t", "WRONGWRONGWRONG1")
    assert SecurityCore.verify_login("old_t", seed)
    raw = open(_file("old_t")).read()
    assert "BEGIN PRIVATE KEY" not in raw and json.loads(raw)["version"] == 2
    SecurityCore.lock()
    assert SecurityCore.verify_login("old_t", seed)   # v2 path now


def test_tofu_pinning_rejects_key_swap(alice):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    def pem():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return k.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    a, b = pem(), pem()
    assert SecurityCore.pin_discovered_contact("bob_t", a, "relay") == "new"
    assert SecurityCore.pin_discovered_contact("bob_t", a, "lan") == "unchanged"
    assert SecurityCore.pin_discovered_contact("bob_t", b, "lan") == "mismatch"
    assert SecurityCore.get_contacts()["bob_t"] == a
    assert SecurityCore.get_contact_info("bob_t")["verified"] is False
    with pytest.raises(IdentityError):
        SecurityCore.pin_discovered_contact("../x", a, "lan")
    with pytest.raises(Exception):
        SecurityCore.pin_discovered_contact("junk_t", "not a key", "lan")
    assert len(public_key_fingerprint(a).split()) == 8
