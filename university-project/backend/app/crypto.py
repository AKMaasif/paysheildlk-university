"""Encryption at rest.

One configured secret (ENCRYPTION_KEY) is never used directly. Each purpose gets its own 32-byte key derived from it with
HKDF-SHA256, so a key found for one use is useless for another.

Two kinds of ciphertext:
  gcm2$  AES-256-GCM with a random nonce. For secrets that are only ever read back (authenticator secret, face data).
  siv1$  AES-256-GCM-SIV with a nonce derived from the purpose. Deterministic: the same value gives the same ciphertext,
         so the database can still find a customer by email, phone or account number. It reveals only that two stored
         values are equal, never what they are.
Older rows written as gcm$ (key made by plain SHA-256) or as plain text still read, and are rewritten on the next save.
"""

import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESGCMSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MIN_KEY_CHARS = 32
_SALT = b"payshield-keys-v2"


class WeakKeyError(ValueError):
    """The encryption key is missing or too short. The service must not run on a guessable key."""


def _material(secret: str) -> bytes:
    text = (secret or "").strip()
    if len(text) < MIN_KEY_CHARS:
        raise WeakKeyError(f"ENCRYPTION_KEY must be at least {MIN_KEY_CHARS} characters (for example 64 hex characters from `openssl rand -hex 32`).")
    if len(text) == 64:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    return text.encode()


def derive(secret: str, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=purpose.encode()).derive(_material(secret))


def check_key(secret: str) -> None:
    """Raise if the key cannot be used. Called at start-up so a bad key stops the service rather than a later request."""
    _material(secret)


def _legacy_key(secret: str) -> bytes:
    return hashlib.sha256((secret or "").encode()).digest()


def encrypt(plaintext: str, secret: str, purpose: str = "") -> str:
    if plaintext is None:
        return ""
    nonce = os.urandom(12)
    token = AESGCM(derive(secret, "gcm:" + purpose)).encrypt(nonce, plaintext.encode(), b"payshield:" + purpose.encode())
    return f"gcm2${nonce.hex()}${token.hex()}"


def decrypt(blob: str, secret: str, purpose: str = "") -> str:
    if not blob:
        return ""
    if blob.startswith("gcm2$"):
        _scheme, nonce_hex, token_hex = blob.split("$", 2)
        plain = AESGCM(derive(secret, "gcm:" + purpose)).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(token_hex), b"payshield:" + purpose.encode())
        return plain.decode()
    if blob.startswith("gcm$"):
        _scheme, nonce_hex, token_hex = blob.split("$", 2)
        return AESGCM(_legacy_key(secret)).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(token_hex), b"payshield").decode()
    return blob


def _siv(secret: str, purpose: str) -> tuple[AESGCMSIV, bytes]:
    return AESGCMSIV(derive(secret, "siv:" + purpose)), derive(secret, "siv-nonce:" + purpose)[:12]


def seal_field(value: str, secret: str, purpose: str) -> str:
    """Deterministic encryption of a short personal field, so it can still be looked up by equality."""
    cipher, nonce = _siv(secret, purpose)
    return "siv1$" + cipher.encrypt(nonce, value.encode(), b"payshield:field:" + purpose.encode()).hex()


def open_field(blob: str, secret: str, purpose: str) -> str:
    if not blob or not blob.startswith("siv1$"):
        return blob or ""
    cipher, nonce = _siv(secret, purpose)
    return cipher.decrypt(nonce, bytes.fromhex(blob[5:]), b"payshield:field:" + purpose.encode()).decode()
