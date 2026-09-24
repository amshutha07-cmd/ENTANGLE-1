"""
ghost_map.py — A.N.Sx Vault | encrypted manifest carried inside a PNG (LSB steganography)

The image is only a *transport disguise*. Confidentiality comes from cryptography:
the manifest (which holds the shard key) is AES-256-GCM encrypted, and that AES key is
RSA-OAEP-wrapped to a specific public key. Without the matching private key the image
is useless, and an image that was never wrapped to anyone can no longer be produced.

Layout (mode 2):  "ANSX" | 0x02 | payload_len(4) | rsa_len(2) | rsa_wrapped_key | nonce(12) | AES-GCM payload
Legacy modes 0/1 are still readable so old ghost maps can be migrated; mode 0 (raw key
stored in the image) is INSECURE and can no longer be created.
"""
from __future__ import annotations

import logging
import math
import os

from PIL import Image
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
_TO_BIT = bytes.maketrans(bytes(range(256)), bytes(b & 1 for b in range(256)))
_ASCII_TO_BIT = bytes.maketrans(b"01", b"\x00\x01")
_MAX_PAYLOAD = 512 * 1024 * 1024


class GhostMapError(ValueError):
    pass


class GhostMap:
    HEADER = b"ANSX"

    # ── bit plumbing (big-int arithmetic; fast for multi-MB payloads) ────────
    @staticmethod
    def _embed(pixels: bytearray, data: bytes) -> bytearray:
        nbits = len(data) * 8
        if nbits > len(pixels):
            raise GhostMapError(
                f"Carrier image too small: need {nbits} bits, image has {len(pixels)} bits of capacity.")
        bits = format(int.from_bytes(data, "big"), f"0{nbits}b").encode().translate(_ASCII_TO_BIT)
        cover = int.from_bytes(pixels[:nbits], "big")
        ones = int.from_bytes(b"\x01" * nbits, "big")
        merged = (cover & ~ones) | int.from_bytes(bits, "big")
        out = bytearray(pixels)
        out[:nbits] = merged.to_bytes(nbits, "big")
        return out

    @staticmethod
    def _extract(pixels: bytes, nbytes: int) -> bytes:
        nbits = nbytes * 8
        if nbits > len(pixels):
            raise GhostMapError("Image too small to contain the announced payload.")
        bitstr = bytes(pixels[:nbits]).translate(_TO_BIT)
        return int(bitstr.translate(bytes.maketrans(b"\x00\x01", b"01")).decode(), 2).to_bytes(nbytes, "big") if nbytes else b""

    @staticmethod
    def required_side(payload_bytes: int, minimum: int = 1200) -> int:
        """Square carrier side (pixels) able to hold `payload_bytes` plus framing overhead."""
        bits = (payload_bytes + 700) * 8
        return max(minimum, math.ceil(math.sqrt(bits / 3)) + 8)

    @classmethod
    def make_carrier(cls, path: str, payload_bytes: int) -> str:
        side = cls.required_side(payload_bytes)
        Image.effect_noise((side, side), 12).convert("RGB").save(path, format="PNG")
        return path

    # ── create ───────────────────────────────────────────────────────────────
    @classmethod
    def hide_payload_in_image(cls, json_payload: str, receiver_pub_key_pem: str,
                              carrier_img_path: str, output_img_path: str) -> None:
        if not (receiver_pub_key_pem and receiver_pub_key_pem.strip().startswith("-----BEGIN")):
            raise GhostMapError("A recipient public key is required: refusing to store the key unencrypted.")
        pub = serialization.load_pem_public_key(receiver_pub_key_pem.encode("utf-8"))

        aes_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        payload = AESGCM(aes_key).encrypt(nonce, json_payload.encode("utf-8"), cls.HEADER)
        wrapped = pub.encrypt(aes_key, _OAEP)

        blob = (cls.HEADER + b"\x02" + len(payload).to_bytes(4, "big")
                + len(wrapped).to_bytes(2, "big") + wrapped + nonce + payload)

        img = Image.open(carrier_img_path).convert("RGB")
        pixels = cls._embed(bytearray(img.tobytes()), blob)
        Image.frombytes("RGB", img.size, bytes(pixels)).save(output_img_path, format="PNG")
        logger.info("Ghost Map forged (RSA+AES-GCM, %d bytes) -> %s", len(blob), output_img_path)

    # ── read ─────────────────────────────────────────────────────────────────
    @classmethod
    def extract_payload_from_image(cls, private_key_pem: str, courier_img_path: str) -> str:
        img = Image.open(courier_img_path).convert("RGB")
        pixels = img.tobytes()

        head = cls._extract(pixels, 9)
        if head[:4] != cls.HEADER:
            raise GhostMapError("Magic header missing — not a Ghost Map image.")
        mode = head[4]
        plen = int.from_bytes(head[5:9], "big")
        if plen > _MAX_PAYLOAD:
            raise GhostMapError("Announced payload is implausibly large.")

        if mode == 2:
            klen = int.from_bytes(cls._extract(pixels, 11)[9:11], "big")
            off = 11
        elif mode == 1:
            klen, off = 512, 9
        elif mode == 0:
            klen, off = 32, 9
            logger.warning("Reading a legacy MODE 0 ghost map: its key is stored unencrypted in the image.")
        else:
            raise GhostMapError(f"Unknown ghost-map mode {mode}.")

        body = cls._extract(pixels, off + klen + 12 + plen)
        key_block = body[off:off + klen]
        nonce = body[off + klen:off + klen + 12]
        payload = body[off + klen + 12:]

        if mode == 0:
            aes_key, aad = key_block, None
        else:
            if not private_key_pem:
                raise GhostMapError("Private key is locked. Log in with your NFC card first.")
            priv = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
            try:
                aes_key = priv.decrypt(key_block, _OAEP)
            except ValueError as exc:
                raise GhostMapError("This ghost map was not encrypted for your identity.") from exc
            aad = cls.HEADER if mode == 2 else None
        try:
            return AESGCM(aes_key).decrypt(nonce, payload, aad).decode("utf-8")
        except Exception as exc:
            raise GhostMapError("Ghost map failed authentication (corrupted or tampered).") from exc
