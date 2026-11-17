import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

COLOMBO = ZoneInfo("Asia/Colombo")

OPERATOR_ROLES = (
    "fraud_analyst",
    "telecom_operator",
    "bank_supervisor",
    "auditor",
    "super_admin",
)

ROLE_LABELS = {
    "fraud_analyst": "Fraud analyst",
    "telecom_operator": "Telecom operator",
    "bank_supervisor": "Bank supervisor",
    "auditor": "Auditor",
    "super_admin": "Super admin",
    "customer": "Customer",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime | None) -> str | None:
    value = aware(dt)
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def colombo_stamp(dt: datetime | None) -> str:
    value = aware(dt)
    if value is None:
        return ""
    return value.astimezone(COLOMBO).strftime("%d %b %Y, %H:%M")


def slt_short(value: datetime | str | None) -> str:
    if isinstance(value, str):
        text = value.strip().replace(" UTC", "").replace("Z", "+00:00")
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return value
    local = aware(value)
    if local is None:
        return ""
    local = local.astimezone(COLOMBO)
    return f"{local.day} {local.strftime('%b')}, {local.strftime('%H:%M')} SLT"


def lkr(amount: int | None) -> str:
    return f"Rs. {int(amount or 0):,}"


def nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def severity(score: int | None) -> str:
    if score is None:
        return "none"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def canonical_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", (phone or "").strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("0"):
        cleaned = "+94" + cleaned[1:]
    elif cleaned.startswith("94"):
        cleaned = "+" + cleaned
    return cleaned


def valid_e164(phone: str) -> bool:
    return re.fullmatch(r"\+[1-9]\d{7,14}", phone or "") is not None


def age_phrase(dt: datetime | None, now: datetime | None = None) -> str:
    value = aware(dt)
    if value is None:
        return "never"
    seconds = (aware(now or utcnow()) - value).total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} h ago" if hours != 1 else "1 h ago"
    days = int(seconds // 86400)
    return f"{days} days ago" if days != 1 else "1 day ago"


def age_hours(dt: datetime | None, now: datetime | None = None) -> float | None:
    value = aware(dt)
    if value is None:
        return None
    return max(0.0, (aware(now or utcnow()) - value).total_seconds() / 3600)


def scoped_bank(role: str, user_bank: str | None, requested: str | None) -> str | None:
    if role == "bank_supervisor" and user_bank:
        return user_bank
    if not requested or requested == "all":
        return None
    return requested
