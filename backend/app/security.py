import hashlib
import hmac
import os
import re

import jwt
import pyotp

from app.util import utcnow


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 20_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 20_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def normalise_answer(answer: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (answer or "").lower())


def hash_answer(answer: str, pepper: str) -> str:
    material = f"{pepper}:{normalise_answer(answer)}".encode()
    return hashlib.sha256(material).hexdigest()


def answers_match(given: str, expected_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_answer(given, pepper), expected_hash)


def create_token(settings, user_id: str, role: str, purpose: str = "access", minutes: int = 720) -> str:
    now = int(utcnow().timestamp())
    payload = {
        "sub": user_id,
        "role": role,
        "purpose": purpose,
        "iat": now,
        "exp": now + minutes * 60,
        "iss": "payshield",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(settings, token: str, purpose: str = "access") -> dict:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="payshield")
    if payload.get("purpose") != purpose:
        raise jwt.InvalidTokenError("unexpected token purpose")
    return payload


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
