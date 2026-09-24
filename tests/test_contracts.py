"""Local-EVM (eth-tester) tests of ANSXRegistry.sol, SessionBroker.sol and the Python broker client."""
import os

import pytest

solcx = pytest.importorskip("solcx")
web3 = pytest.importorskip("web3")
pytest.importorskip("eth_tester")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from eth_account import Account  # noqa: E402
from web3 import Web3, EthereumTesterProvider  # noqa: E402
from eth_tester.exceptions import TransactionFailed  # noqa: E402
from web3.exceptions import ContractLogicError  # noqa: E402

REVERTS = (ContractLogicError, TransactionFailed)

import session_broker  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _compile(fname, name):
    try:
        with open(os.path.join(ROOT, fname)) as f:
            r = solcx.compile_source(f.read(), output_values=["abi", "bin"], solc_version="0.8.0")
    except Exception as exc:  # solc not installed / offline
        pytest.skip(f"solc 0.8.0 unavailable: {exc}")
    return r[[k for k in r if k.endswith(":" + name)][0]]


def _rsa():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode(),
            k.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode())


@pytest.fixture(scope="module")
def chain():
    w3 = Web3(EthereumTesterProvider())
    deployer = w3.eth.accounts[0]

    def deploy(iface):
        c = w3.eth.contract(abi=iface["abi"], bytecode=iface["bin"])
        rcpt = w3.eth.wait_for_transaction_receipt(c.constructor().transact({"from": deployer}))
        return w3.eth.contract(address=rcpt.contractAddress, abi=iface["abi"])

    registry = deploy(_compile("ANSXRegistry.sol", "ANSXRegistry"))
    broker = deploy(_compile("SessionBroker.sol", "SessionBroker"))

    wallets = {}
    for name in ("alice", "bob", "eve"):
        acct = Account.create()
        w3.eth.wait_for_transaction_receipt(
            w3.eth.send_transaction({"from": deployer, "to": acct.address, "value": w3.to_wei(5, "ether")}))
        priv, pub = _rsa()
        wallets[name] = {"acct": acct, "rsa_priv": priv, "rsa_pub": pub}
    return w3, registry, broker, wallets


def _send(w3, fn, acct):
    tx = fn.build_transaction({"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address),
                               "gas": 3_000_000, "gasPrice": w3.eth.gas_price, "chainId": w3.eth.chain_id})
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction))


def test_registry_binds_name_to_own_wallet(chain):
    w3, registry, _, w = chain
    a, b, e = w["alice"]["acct"], w["bob"]["acct"], w["eve"]["acct"]

    assert _send(w3, registry.functions.registerProfile("alice", w["alice"]["rsa_pub"], "1.2.3.4", a.address), a).status == 1
    assert _send(w3, registry.functions.registerProfile("bob", w["bob"]["rsa_pub"], "", b.address), b).status == 1

    # Eve cannot register a profile that names someone else's wallet, nor squat an existing name.
    with pytest.raises(REVERTS):
        registry.functions.registerProfile("eve", "k", "", a.address).call({"from": e.address})
    with pytest.raises(REVERTS):
        registry.functions.registerProfile("alice", "k", "", e.address).call({"from": e.address})
    # Only the owner can update the IP.
    with pytest.raises(REVERTS):
        registry.functions.updateIP("alice", "6.6.6.6").call({"from": e.address})
    assert _send(w3, registry.functions.updateIP("alice", "5.5.5.5"), a).status == 1
    assert registry.functions.getIPAddress("alice").call() == "5.5.5.5"
    assert registry.functions.getEthAddress("alice").call() == a.address


def test_session_code_is_encrypted_and_sender_is_verified(chain, monkeypatch):
    w3, registry, broker, w = chain
    a, b, e = w["alice"]["acct"], w["bob"]["acct"], w["eve"]["acct"]

    client = session_broker.SessionBrokerClient.__new__(session_broker.SessionBrokerClient)
    client._w3, client._broker, client._registry, client._connected = w3, broker, registry, True
    monkeypatch.setattr(session_broker, "CHAIN_ID", w3.eth.chain_id)

    ids = {
        "alice": {"eth_address": a.address, "eth_private_key": a.key.hex()},
        "bob": {"eth_address": b.address, "eth_private_key": b.key.hex(), "private_key": w["bob"]["rsa_priv"]},
        "eve": {"eth_address": e.address, "eth_private_key": e.key.hex()},
    }
    monkeypatch.setattr(session_broker, "_load_identity", lambda u: ids.get(u))

    class _Engine:
        def resolve_trusted_public_key(self, user):
            return w[user]["rsa_pub"]

    import web3_bridge
    monkeypatch.setattr(web3_bridge, "get_web3_engine", lambda: _Engine())

    assert client.post_offer("alice", "bob", "ABC123", ttl=300)

    # The public chain never sees the code in the clear.
    raw = broker.functions.getOffers(b.address).call()
    assert raw and b"ABC123" not in bytes(raw[0][2])

    offers = client.poll_active_offers("bob")
    assert [(o["sender"], o["session_code"]) for o in offers] == [("alice", "ABC123")]

    # Eve posts an offer to Bob while CLAIMING to be alice: her wallet is not alice's registry owner.
    _send(w3, broker.functions.postOffer(
        b.address, "alice", session_broker.encrypt_session_code(w["bob"]["rsa_pub"], "EVIL01"), 300), e)
    codes = [o["session_code"] for o in client.poll_active_offers("bob")]
    assert "EVIL01" not in codes and "ABC123" in codes

    # Locked (no private key in memory) -> nothing is decrypted.
    ids["bob"] = {"eth_address": b.address}
    assert client.poll_active_offers("bob") == []
