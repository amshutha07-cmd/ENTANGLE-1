"""Sender signatures on packages: who really sent it, and no forging, tampering or re-sealing for someone else."""
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import engine
import vault_service as vs
from cloud_dispatcher import CloudDispatcher
from security_core import SecurityCore


def _person(name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "operator": name,
        "private_key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode(),
        "public_key": key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
    }


@pytest.fixture(scope="module")
def who():
    return {n: _person(n) for n in ("alice", "bob", "carol", "mallory")}


def _book(who, verified=("alice",)):
    return lambda name: (who[name]["public_key"], "verified" if name in verified else "unverified") if name in who else None


MANIFEST = {"version": 2, "original_file": "plan.pdf", "ephemeral_key": "ab" * 32, "shard_payload": {"fragment_1.ansx": "AAAA"}}


def test_signed_package_names_its_sender(who):
    m = vs.sign_manifest(MANIFEST, who["alice"], who["bob"]["public_key"])
    assert vs.check_sender(m, who["bob"]["private_key"], _book(who)) == {"signer": "alice", "status": "verified"}
    assert vs.check_sender(m, who["bob"]["private_key"], _book(who, verified=()))["status"] == "unverified"
    assert vs.check_sender(m, who["bob"]["private_key"], lambda n: None) == {"signer": "alice", "status": "unknown"}


def test_older_unsigned_packages_still_open(who):
    assert vs.check_sender(dict(MANIFEST), who["bob"]["private_key"], _book(who)) == {"signer": "", "status": "unsigned"}


def test_tampering_with_any_field_is_caught(who):
    m = vs.sign_manifest(MANIFEST, who["alice"], who["bob"]["public_key"])
    for field, value in (("original_file", "invoice.exe"), ("ephemeral_key", "cd" * 32)):
        with pytest.raises(vs.VaultError, match="does not match alice"):
            vs.check_sender({**m, field: value}, who["bob"]["private_key"], _book(who))


def test_someone_else_cannot_sign_in_your_name(who):
    forged = vs.sign_manifest(MANIFEST, {**who["mallory"], "operator": "alice"}, who["bob"]["public_key"])
    with pytest.raises(vs.VaultError, match="does not match alice"):
        vs.check_sender(forged, who["bob"]["private_key"], _book(who))


def test_a_package_passed_on_to_a_third_person_is_refused(who):
    m = vs.sign_manifest(MANIFEST, who["alice"], who["bob"]["public_key"])     # bob re-seals alice's package for carol
    with pytest.raises(vs.VaultError, match="for someone else"):
        vs.check_sender(m, who["carol"]["private_key"], _book(who))


def test_relay_sender_must_match_the_signer(who):
    m = vs.sign_manifest(MANIFEST, who["mallory"], who["bob"]["public_key"])
    with pytest.raises(vs.VaultError, match="claims to come from alice"):
        vs.check_sender(m, who["bob"]["private_key"], _book(who), expected_sender="alice")


def test_malformed_signature_blocks_are_refused(who):
    m = vs.sign_manifest(MANIFEST, who["alice"], who["bob"]["public_key"])
    for bad in ("x", {**m["signature"], "alg": "none"}, {**m["signature"], "signer": "../alice"},
                {**m["signature"], "sig": "not base64!"}):
        with pytest.raises(vs.VaultError):
            vs.check_sender({**m, "signature": bad}, who["bob"]["private_key"], _book(who))


@pytest.mark.skipif(not engine.available(), reason="native engine not built")
def test_protect_send_and_receive_carry_the_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(SecurityCore, "_publish_identity", classmethod(lambda cls, *a, **k: None))
    for name in ("sig_sender", "sig_receiver"):
        SecurityCore.establish_identity(name, f"seed-for-{name}-xx", auth="passphrase")
    sender, receiver = (SecurityCore.load_identity_for_user(n) for n in ("sig_sender", "sig_receiver"))
    src = tmp_path / "notes.txt"
    src.write_bytes(os.urandom(20_000))
    none = CloudDispatcher(targets=[])

    res = vs.protect(str(src), sender, dispatcher=none)
    own = vs.reconstruct(res.entry["ghost_map_path"], sender["private_key"], str(tmp_path / "own"), dispatcher=none,
                         expected_sender="sig_sender")
    assert (own["signer"], own["signature"]) == ("sig_sender", "local")

    sent = vs.rewrap_for(res.entry, sender, receiver["public_key"], dispatcher=none)
    got = vs.reconstruct(sent, receiver["private_key"], str(tmp_path / "got"), dispatcher=none, expected_sender="sig_sender")
    assert (got["signer"], got["signature"]) == ("sig_sender", "local")         # both identities live on this computer
    assert (tmp_path / "got").read_bytes() == src.read_bytes()
