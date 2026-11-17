import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(material: str) -> bytes:
    raw = (material or "payshield-dev-encryption-key").encode()
    return hashlib.sha256(raw).digest()


def encrypt(plaintext: str, material: str) -> str:
    if plaintext is None:
        return ""
    nonce = os.urandom(12)
    token = AESGCM(_key(material)).encrypt(nonce, plaintext.encode(), b"payshield")
    return f"gcm${nonce.hex()}${token.hex()}"


def decrypt(blob: str, material: str) -> str:
    if not blob:
        return ""
    if not blob.startswith("gcm$"):
        return blob
    _scheme, nonce_hex, token_hex = blob.split("$", 2)
    plain = AESGCM(_key(material)).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(token_hex), b"payshield")
    return plain.decode()
