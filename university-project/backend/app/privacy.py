"""What may leave the bank for a hosted model.

Agents write their facts in plain text. Before any of it reaches Gemini or the
fallback model, personal identifiers are masked here, and text that carries a
secret (a password, a security answer, an authenticator code) is refused
outright. The mock engine gets the same masked text, so tests see what a real
provider would see.
"""

import re

# Order matters: the longer, more specific shapes go first.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # Sri Lankan mobile and land lines in international or local form.
    ("phone", re.compile(r"(?<!\d)(?:\+94|0094|0)\s?\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)")),
    # New (12 digits) and old (9 digits + V/X) national identity card numbers.
    ("nic", re.compile(r"(?<![\w])(?:\d{12}|\d{9}[VvXx])(?![\w])")),
    # Card and account numbers: 8 to 19 digits, optionally grouped by spaces.
    # Dashes are not allowed so ISO dates such as 2026-11-17 are left alone.
    ("account", re.compile(r"(?<![\d-])\d(?: ?\d){7,18}(?![\d-])")),
]

# Field labels that must never be sent to a model, whatever their value.
_SECRET_LABELS = re.compile(
    r"\b(password|passcode|pin|otp|totp|authenticator code|security answer|answer_hash|secret|private key)\s*[:=]",
    re.IGNORECASE,
)


class PrivacyViolation(ValueError):
    """Raised when text bound for a model carries a secret."""


def _tail(value: str, keep: int = 2) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[-keep:] if digits else ""


def mask(text: str) -> str:
    """Replace personal identifiers with typed placeholders that keep the last two digits."""
    if not text:
        return text
    out = text
    for kind, pattern in _PATTERNS:
        if kind == "email":
            out = pattern.sub("[email]", out)
        else:
            out = pattern.sub(lambda m, k=kind: f"[{k} ..{_tail(m.group(0))}]", out)
    return out


def guard(text: str) -> str:
    """Refuse secrets, then mask identifiers. Returns the text that may be sent."""
    if text and _SECRET_LABELS.search(text):
        raise PrivacyViolation("Text for the model contains a secret field and was not sent.")
    return mask(text)
