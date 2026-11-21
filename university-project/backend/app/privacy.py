"""What may leave the bank for a hosted model.

Agents write their facts in plain text. Before any of it reaches Gemini or the
fallback model, personal identifiers are masked here, and text that carries a
secret (a password, a security answer, an authenticator code) is refused
outright. The mock engine gets the same masked text, so tests see what a real
provider would see.
"""

import re
import unicodedata

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


_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_ROLE_MARKER = re.compile(r"\b(system|assistant|developer|human|user|instruction[s]?)\s*:", re.IGNORECASE)


def untrusted(text, limit: int = 80) -> str:
    """Text typed by a third party, such as a payee name, made safe to place in a prompt.

    It is normalised (full-width letters become plain ones), stripped of control, zero-width and
    direction-changing characters, reduced to one line, stripped of markup brackets, cut short, and
    wrapped in guillemets. The system prompt tells the model that anything inside them is data, never
    an instruction. This lowers the chance that hostile text steers a model. It is not a guarantee,
    which is why no model output can change a hold, block or release.
    """
    value = unicodedata.normalize("NFKC", "" if text is None else str(text))
    value = _INVISIBLE.sub("", value)
    value = "".join(" " if unicodedata.category(c).startswith("C") else c for c in value)
    value = re.sub(r"[<>{}\[\]`\u00ab\u00bb]", " ", value)
    value = _ROLE_MARKER.sub(lambda m: m.group(1) + " -", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "\u2026"
    return "\u00ab" + value + "\u00bb"


# Keys whose values are never stored in a trace or shown in the activity terminal, whatever they hold.
_NEVER_SHOWN = re.compile(
    r"(password|passcode|pin\b|otp|totp|secret|token|api_?key|authorization|answer|embedding|template|face_ref|face_hash|signature|private|credential|jwt|hash)",
    re.IGNORECASE,
)


def redact(value, depth: int = 0):
    """A copy of a tool's structured result that is safe to store and show: secret-looking keys are dropped,
    identifiers in text are masked, and very long values are cut. Booleans, numbers and short codes pass through."""
    if depth > 6:
        return "[deep]"
    if isinstance(value, dict):
        return {str(k): redact(v, depth + 1) for k, v in value.items() if not _NEVER_SHOWN.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:25]]
    if isinstance(value, str):
        return mask(value)[:300]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return mask(str(value))[:300]
