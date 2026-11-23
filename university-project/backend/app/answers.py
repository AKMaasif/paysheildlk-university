import hashlib
import hmac
import re
import unicodedata
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# One scheme for every security answer: Argon2id with its own random salt (the OWASP minimum cost, because a round
# checks several answers). Hashes written by older versions still verify and are replaced on the next correct answer.
_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)
_ROUNDS = 20_000  # the old PBKDF2 setting, kept only to read old rows

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
    """The one clean-up every answer goes through, when it is saved and when it is checked: compatibility-normalise, lower-case,
    and keep only letters and digits, so capitals, spaces and punctuation never decide a match. Dates are read as dates."""
    text = unicodedata.normalize("NFKC", answer or "").casefold().strip()
    if code == "dob":
        return normalise_dob(text)
    return "".join(ch for ch in text if ch.isalnum())


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
    return _hasher.hash(normalise_answer(answer, code))


def is_current(stored: str) -> bool:
    return (stored or "").startswith("$argon2id$")


def verify_secret(answer: str, stored: str, code: str = "", pepper: str = "") -> bool:
    """True when the answer matches. Reads the current Argon2id format and the two older ones."""
    stored = stored or ""
    if is_current(stored):
        try:
            return _hasher.verify(stored, normalise_answer(answer, code))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    if stored.startswith("pbkdf2$"):  # version 1: salted PBKDF2, whitespace-only clean-up
        try:
            _scheme, salt_hex, digest_hex = stored.split("$")
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            return False
        old = re.sub(r"\s+", " ", (answer or "").strip().lower())
        if code == "dob":
            old = normalise_dob(old)
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", old.encode(), salt, _ROUNDS).hex(), digest_hex)
    if re.fullmatch(r"[0-9a-f]{64}", stored):  # version 0: unsalted SHA-256 with a fixed pepper (old demonstration rows)
        old = re.sub(r"[^a-z0-9]", "", (answer or "").lower())
        return hmac.compare_digest(hashlib.sha256(f"{pepper}:{old}".encode()).hexdigest(), stored)
    return False
