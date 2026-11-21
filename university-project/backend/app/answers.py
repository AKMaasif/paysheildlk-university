import hashlib
import hmac
import os
import re
from datetime import datetime

# Per-answer PBKDF2-SHA256. The salt is stored beside the digest.
_ROUNDS = 20_000

QUESTION_BANK = [
    {"code": "colour", "prompt": "What is your favourite colour?"},
    {"code": "food", "prompt": "What is your favourite food?"},
    {"code": "father", "prompt": "What is your father's first name?"},
    {"code": "dob", "prompt": "What is your date of birth?"},
    {"code": "mother", "prompt": "What is your mother's maiden name?"},
    {"code": "school", "prompt": "What was the name of your first school?"},
    {"code": "city", "prompt": "In which town were you born?"},
    {"code": "pet", "prompt": "What was the name of your first pet?"},
]


def normalise_answer(answer: str, code: str = "") -> str:
    text = re.sub(r"\s+", " ", (answer or "").strip().lower())
    if code == "dob":
        return normalise_dob(text)
    return text


def normalise_dob(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        # Day-month-year when the year is the last four digits.
        if digits[4:].startswith(("19", "20")):
            return f"{digits[4:]}-{digits[2:4]}-{digits[0:2]}"
    return text.lower()


def hash_secret(answer: str, code: str = "") -> str:
    salt = os.urandom(16)
    material = normalise_answer(answer, code).encode()
    digest = hashlib.pbkdf2_hmac("sha256", material, salt, _ROUNDS)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_secret(answer: str, stored: str, code: str = "") -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    material = normalise_answer(answer, code).encode()
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", material, salt, _ROUNDS)
    return hmac.compare_digest(digest.hex(), digest_hex)
