import hashlib
import hmac
import os
import re

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.util import utcnow

# Argon2id with the argon2-cffi defaults (RFC 9106 low-memory profile).
_hasher = PasswordHasher()

# Access tokens are short. A refresh token renews them while the person keeps using the app.
ACCESS_MINUTES = 30
REFRESH_MINUTES = 12 * 60


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify_legacy_pbkdf2(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 20_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def verify_password(password: str, stored: str) -> bool:
    stored = stored or ""
    if stored.startswith("$argon2"):
        try:
            return _hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return _verify_legacy_pbkdf2(password, stored)


def password_needs_upgrade(stored: str) -> bool:
    """True for legacy PBKDF2 hashes or Argon2 hashes made with weaker parameters."""
    stored = stored or ""
    if not stored.startswith("$argon2"):
        return True
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return True


def normalise_answer(answer: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (answer or "").lower())


def hash_answer(answer: str, pepper: str) -> str:
    material = f"{pepper}:{normalise_answer(answer)}".encode()
    return hashlib.sha256(material).hexdigest()


def answers_match(given: str, expected_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_answer(given, pepper), expected_hash)


def create_token(settings, user_id: str, role: str, purpose: str = "access", minutes: int = ACCESS_MINUTES) -> str:
    now = int(utcnow().timestamp())
    payload = {
        "sub": user_id,
        "role": role,
        "purpose": purpose,
        "iat": now,
        "exp": now + minutes * 60,
        "iss": "payshield",
        "jti": os.urandom(8).hex(),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(settings, token: str, purpose: str = "access") -> dict:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="payshield")
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("unexpected token purpose")
    return payload


def seal_totp(secret: str) -> str:
    """Authenticator secrets are stored AES-256-GCM encrypted."""
    from app.crypto import encrypt
    from app.runtime import runtime

    return encrypt(secret, runtime.settings.encryption_key)


def open_totp(stored: str | None) -> str:
    """Decrypt a stored authenticator secret. Older plain rows still read."""
    from app.crypto import decrypt
    from app.runtime import runtime

    return decrypt(stored or "", runtime.settings.encryption_key)


def totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def totp_valid(secret: str, code: str) -> bool:
    cleaned = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", cleaned):
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=1)


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="PayShield")


def seconds_left_in_totp_window() -> int:
    return 30 - (int(utcnow().timestamp()) % 30)
