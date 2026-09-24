"""
session_broker.py — A.N.Sx Vault | Blockchain Session Signaling
================================================================

Wraps web3.py to interact with SessionBroker.sol on Polygon Amoy testnet.

Flow:
  SENDER:   post_offer(sender_username, recipient_username, session_code)
            → resolves recipient's ETH address from ANSXRegistry on-chain
            → calls SessionBroker.postOffer() → tx confirmed ~2 seconds

  RECEIVER: poll_active_offers(my_username)
            → calls SessionBroker.getActiveOffers(my_eth_address)
            → decrypts each code with the operator's RSA key, verifies the sender
              against ANSXRegistry, returns list of {index, sender, session_code, expiry}

  After connect: claim_offer(my_username, offer_index)
            → marks offer consumed on-chain to prevent replay
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Amoy Testnet Config ───────────────────────────────────────────────────────

AMOY_RPC = "https://rpc-amoy.polygon.technology"
CHAIN_ID  = 80002   # Polygon Amoy

# ── ABI Fragments (only what we need) ────────────────────────────────────────

SESSION_BROKER_ABI = [
    {
        "inputs": [
            {"name": "recipient",      "type": "address"},
            {"name": "senderUsername", "type": "string"},
            {"name": "encryptedCode",  "type": "bytes"},
            {"name": "ttlSeconds",     "type": "uint256"},
        ],
        "name": "postOffer",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "recipient", "type": "address"}],
        "name": "getActiveOffers",
        "outputs": [
            {"name": "indices",         "type": "uint256[]"},
            {"name": "senders",         "type": "address[]"},
            {"name": "senderUsernames", "type": "string[]"},
            {"name": "encryptedCodes",  "type": "bytes[]"},
            {"name": "expiries",        "type": "uint256[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "index", "type": "uint256"}],
        "name": "claimOffer",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "recipient", "type": "address"}],
        "name": "getOfferCount",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "recipient",      "type": "address"},
            {"indexed": True,  "name": "sender",         "type": "address"},
            {"indexed": False, "name": "senderUsername", "type": "string"},
            {"indexed": False, "name": "expiry",         "type": "uint256"},
        ],
        "name": "OfferPosted",
        "type": "event",
    },
]

ANSX_REGISTRY_ABI = [
    {
        "inputs": [{"name": "username", "type": "string"}],
        "name": "getEthAddress",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "username", "type": "string"},
            {"name": "pubKey",   "type": "string"},
            {"name": "ipAddr",   "type": "string"},
            {"name": "ethAddr",  "type": "address"},
        ],
        "name": "registerProfile",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "username", "type": "string"}],
        "name": "isUserRegistered",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ── Settings loader ───────────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        import settings as s
        return {
            "session_broker_address": getattr(s, "SESSION_BROKER_ADDRESS", ""),
            "ansx_registry_address":  getattr(s, "ANSX_REGISTRY_ADDRESS", ""),
        }
    except Exception:
        return {}


def _load_identity(username: str) -> Optional[dict]:
    """Public identity always; eth_private_key only after a successful NFC login this session."""
    from security_core import SecurityCore
    return SecurityCore.load_identity_for_user(username)




def _oaep():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def encrypt_session_code(recipient_pub_pem: str, code: str) -> bytes:
    """The chain is public: the relay session code only ever goes on-chain encrypted to the recipient."""
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(recipient_pub_pem.encode())
    return pub.encrypt(code.encode(), _oaep())


def decrypt_session_code(private_pem: str, blob: bytes) -> Optional[str]:
    from cryptography.hazmat.primitives import serialization
    try:
        priv = serialization.load_pem_private_key(private_pem.encode(), password=None)
        return priv.decrypt(bytes(blob), _oaep()).decode()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SessionBroker Client
# ═══════════════════════════════════════════════════════════════════════════════

class SessionBrokerClient:
    """
    Python interface to SessionBroker.sol + ANSXRegistry.sol on Polygon Amoy.

    Usage:
        client = SessionBrokerClient()

        # Sender side
        tx = client.post_offer("alice", "bob", "A3XK9Q", ttl=300)

        # Receiver side (called by poller)
        offers = client.poll_active_offers("bob")
        # → [{"index": 0, "sender": "alice", "session_code": "A3XK9Q", "expiry": 1234567890}]

        # After connecting, mark consumed
        client.claim_offer("bob", 0)
    """

    def __init__(self):
        self._w3        = None
        self._broker    = None
        self._registry  = None
        self._connected = False
        self._connect()

    def _connect(self):
        try:
            from web3 import Web3
            cfg = _load_settings()
            broker_addr   = cfg.get("session_broker_address", "")
            registry_addr = cfg.get("ansx_registry_address", "")

            if not broker_addr or not registry_addr:
                logger.warning(
                    "[SessionBroker] Contract addresses not set in settings.py. "
                    "Run deploy_contracts.py first."
                )
                return

            self._w3 = Web3(Web3.HTTPProvider(AMOY_RPC))
            if not self._w3.is_connected():
                logger.error("[SessionBroker] Cannot connect to Polygon Amoy RPC: %s", AMOY_RPC)
                return

            self._broker   = self._w3.eth.contract(
                address=Web3.to_checksum_address(broker_addr),
                abi=SESSION_BROKER_ABI
            )
            self._registry = self._w3.eth.contract(
                address=Web3.to_checksum_address(registry_addr),
                abi=ANSX_REGISTRY_ABI
            )
            self._connected = True
            logger.info("[SessionBroker] Connected to Amoy. Broker: %s", broker_addr)

        except Exception as e:
            logger.error("[SessionBroker] Connection failed: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Resolve ETH address from ANSX username ────────────────────────────────

    def resolve_eth_address(self, username: str) -> Optional[str]:
        """
        Look up a user's ETH address from ANSXRegistry on-chain.
        Falls back to local identity JSON if registry lookup fails.
        """
        # Try on-chain first
        if self._connected:
            try:
                addr = self._registry.functions.getEthAddress(username).call()
                if addr and addr != "0x" + "0" * 40:
                    return addr
            except Exception as e:
                logger.warning("[SessionBroker] Registry lookup failed for '%s': %s", username, e)

        # Fallback: local identity file (for same-machine tests)
        identity = _load_identity(username)
        if identity:
            return identity.get("eth_address")

        return None

    # ── Post offer (sender side) ──────────────────────────────────────────────

    def post_offer(
        self,
        sender_username:    str,
        recipient_username: str,
        session_code:       str,
        ttl:                int = 300,
    ) -> Optional[str]:
        """
        Post a session offer on-chain, addressed to the recipient's ETH address.
        Returns the transaction hash on success, None on failure.
        """
        if not self._connected:
            logger.error("[SessionBroker] Not connected — cannot post offer.")
            return None

        identity = _load_identity(sender_username)
        if not identity:
            raise ValueError(f"Identity for '{sender_username}' not found locally.")
        sender_private_key = identity.get("eth_private_key")
        if not sender_private_key:
            raise ValueError("Wallet is locked or missing. Log in with your NFC card (or re-register) first.")

        recipient_eth = self.resolve_eth_address(recipient_username)
        if not recipient_eth:
            raise ValueError(
                f"Cannot resolve ETH address for '{recipient_username}'. "
                "Are they registered on the blockchain?"
            )

        # Encrypt the session code to the recipient's PINNED public key.
        import web3_bridge
        recipient_pub = web3_bridge.get_web3_engine().resolve_trusted_public_key(recipient_username)
        encrypted_code = encrypt_session_code(recipient_pub, session_code)

        from web3 import Web3
        sender_account = self._w3.eth.account.from_key(sender_private_key)
        balance = self._w3.eth.get_balance(sender_account.address)
        gas_price = self._w3.eth.gas_price
        fn = self._broker.functions.postOffer(
            Web3.to_checksum_address(recipient_eth), sender_username, encrypted_code, ttl)
        gas = int(fn.estimate_gas({"from": sender_account.address}) * 1.3)
        if balance < gas * gas_price:
            raise ValueError(
                f"Wallet {sender_account.address} has too little test MATIC to post an offer. "
                f"Fund it: {self.get_faucet_url(sender_username)}")

        tx = fn.build_transaction({
            "chainId":  CHAIN_ID,
            "from":     sender_account.address,
            "nonce":    self._w3.eth.get_transaction_count(sender_account.address),
            "gas":      gas,
            "gasPrice": gas_price,
        })

        signed = self._w3.eth.account.sign_transaction(tx, sender_private_key)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)

        logger.info(
            "[SessionBroker] Offer posted on Amoy | tx: %s | sender: %s → recipient: %s",
            tx_hash.hex(), sender_username, recipient_username
        )

        # Wait for confirmation (max 30s)
        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt.status == 1:
                logger.info("[SessionBroker] Tx confirmed in block %d.", receipt.blockNumber)
            else:
                logger.error("[SessionBroker] Tx FAILED (reverted).")
                return None
        except Exception as e:
            logger.warning("[SessionBroker] Tx confirmation timeout: %s", e)

        return tx_hash.hex()

    # ── Poll active offers (receiver side) ───────────────────────────────────

    def poll_active_offers(self, username: str) -> list[dict]:
        """
        Active (unclaimed, unexpired) offers for this user, decrypted with their unlocked RSA key.
        Offers whose on-chain sender is not the registry owner of the claimed username are dropped
        (anyone can post to anyone's inbox; only registry-bound senders are honoured).
        Each entry: {"index", "sender", "sender_address", "session_code", "expiry"}
        """
        if not self._connected:
            return []
        identity = _load_identity(username)
        if not identity or not identity.get("eth_address"):
            return []
        private_pem = identity.get("private_key")
        if not private_pem:
            return []   # locked: cannot decrypt anything yet

        try:
            from web3 import Web3
            indices, sender_addrs, senders, blobs, expiries = (
                self._broker.functions.getActiveOffers(Web3.to_checksum_address(identity["eth_address"])).call()
            )
            offers, now = [], int(time.time())
            for i, idx in enumerate(indices):
                if expiries[i] <= now:
                    continue
                try:
                    registered = self._registry.functions.getEthAddress(senders[i]).call()
                except Exception:
                    registered = None
                if not registered or registered.lower() != sender_addrs[i].lower():
                    logger.warning("[SessionBroker] Dropping offer #%d: sender %s is not the registered owner of '%s'.",
                                   idx, sender_addrs[i], senders[i])
                    continue
                code = decrypt_session_code(private_pem, blobs[i])
                if not code or len(code) != 6:
                    logger.warning("[SessionBroker] Dropping offer #%d: cannot decrypt.", idx)
                    continue
                offers.append({"index": int(idx), "sender": senders[i], "sender_address": sender_addrs[i],
                               "session_code": code, "expiry": expiries[i]})
            return offers
        except Exception as e:
            logger.error("[SessionBroker] poll_active_offers failed: %s", e)
            return []

    # ── Claim offer (receiver, after connecting) ──────────────────────────────

    def claim_offer(self, username: str, offer_index: int) -> bool:
        """
        Mark an offer as claimed on-chain to prevent replay attacks.
        """
        if not self._connected:
            return False

        identity = _load_identity(username)
        if not identity:
            return False

        private_key = identity.get("eth_private_key")
        if not private_key:
            return False

        try:
            from web3 import Web3
            account = self._w3.eth.account.from_key(private_key)
            nonce   = self._w3.eth.get_transaction_count(account.address)

            fn = self._broker.functions.claimOffer(offer_index)
            tx = fn.build_transaction({
                "chainId":  CHAIN_ID,
                "from":     account.address,
                "nonce":    nonce,
                "gas":      int(fn.estimate_gas({"from": account.address}) * 1.3),
                "gasPrice": self._w3.eth.gas_price,
            })

            signed  = self._w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            logger.info("[SessionBroker] Offer #%d claimed. tx: %s", offer_index, tx_hash.hex())
            return True

        except Exception as e:
            logger.error("[SessionBroker] claim_offer failed: %s", e)
            return False

    # ── Get faucet URL for this user ──────────────────────────────────────────

    def get_faucet_url(self, username: str) -> str:
        identity = _load_identity(username)
        addr = (identity or {}).get("eth_address", "")
        return f"https://faucet.polygon.technology/?network=amoy&address={addr}"

    # ── Get Polygonscan tx URL ────────────────────────────────────────────────

    @staticmethod
    def tx_url(tx_hash: str) -> str:
        return f"https://amoy.polygonscan.com/tx/{tx_hash}"

    # ── Check MATIC balance ───────────────────────────────────────────────────

    def get_balance_matic(self, username: str) -> float:
        if not self._connected:
            return 0.0
        identity = _load_identity(username)
        if not identity:
            return 0.0
        eth_addr = identity.get("eth_address", "")
        if not eth_addr:
            return 0.0
        try:
            from web3 import Web3
            bal_wei = self._w3.eth.get_balance(Web3.to_checksum_address(eth_addr))
            return float(self._w3.from_wei(bal_wei, "ether"))
        except Exception:
            return 0.0


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: Optional[SessionBrokerClient] = None

def get_session_broker() -> SessionBrokerClient:
    global _client
    if _client is None:
        _client = SessionBrokerClient()
    return _client
