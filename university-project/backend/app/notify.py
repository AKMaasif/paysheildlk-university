import logging
import re
import smtplib
import threading
import time
from collections import deque
from email.message import EmailMessage

import httpx
import jwt

from app.db import session_scope
from app.models import Device, Notification, User
from app.util import nid, utcnow

log = logging.getLogger("payshield.notify")

GENERIC_SMS = (
    "PayShield: an unusual transaction on your account was held. "
    "Open the PayShield app to review. "
    "We will never ask you to share a code by text message."
)

BLOCKED_SMS = (
    "PayShield: We blocked a payment attempt after a SIM change on your number. "
    "Open the PayShield app on your registered phone for the next step. "
    "We will never ask you to share a code by text message."
)

_URL = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_CODE = re.compile(r"\b\d{4,8}\b")

# What can be known about getting a message to a person. The states, in order of how much they prove:
#   logged_only              written to the server log. Nothing left this system.
#   inbox_stored             kept in the customer's PayShield inbox. Nothing external was asked.
#   blocked_not_allowlisted  a real provider is configured but this recipient is not on the demonstration allow-list
#   blocked_budget           the hourly limit on real messages was reached
#   provider_accepted        the provider took the request (HTTP 2xx or SMTP accepted). Not proof it reached the person.
#   failed                   the provider refused or could not be reached; see reason and retryable
#   delivered                the provider confirmed delivery. No integration here reports this, so it is never shown today.
CONFIRMATION_LIMITS = {
    "sms": "Text.lk accepted the request. Delivery to the handset is not reported back to PayShield, so it is unconfirmed.",
    "email": "The mail server accepted the message for relay. Delivery to the mailbox is not reported back, so it is unconfirmed.",
    "push": "Firebase accepted the message. Delivery to the device is not reported back, so it is unconfirmed.",
}

_budget: dict[str, deque] = {}
_budget_lock = threading.Lock()

_fcm_token: str | None = None
_fcm_expiry: float = 0


class UnsafeSms(ValueError):
    pass


def assert_safe_sms(message: str) -> None:
    if _URL.search(message or ""):
        raise UnsafeSms("SMS must not contain a link")
    if _CODE.search(message or ""):
        raise UnsafeSms("SMS must not contain a code")


def _store(user_id: str, case_id: str | None, channel: str, title: str, body: str, data: dict | None) -> str:
    note_id = nid("ntf")
    with session_scope() as session:
        session.add(
            Notification(
                id=note_id,
                user_id=user_id,
                case_id=case_id,
                channel=channel,
                title=title,
                body=body,
                data=data,
                created_at=utcnow(),
            )
        )
    return note_id


def _fcm_access_token(settings) -> str:
    global _fcm_token, _fcm_expiry
    now = time.time()
    if _fcm_token and now < _fcm_expiry - 60:
        return _fcm_token
    issued = int(now)
    assertion = jwt.encode(
        {
            "iss": settings.firebase_client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": issued,
            "exp": issued + 3600,
        },
        settings.firebase_private_key,
        algorithm="RS256",
    )
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=15,
    )
    response.raise_for_status()
    _fcm_token = response.json()["access_token"]
    _fcm_expiry = now + 3500
    return _fcm_token


def delivery(state: str, channel: str, *, inbox: bool = False, provider: str | None = None, message_id: str | None = None, reason: str | None = None,
             retryable: bool = False, note: str | None = None) -> dict:
    """The structured record of what happened to one message. `confirmed` is true only when a provider says it delivered."""
    return {
        "state": state,
        "channel": channel,
        "confirmed": state == "delivered",
        "inbox_stored": inbox,
        "provider": provider,
        "provider_message_id": message_id,
        "reason": reason,
        "retryable": retryable,
        "note": note or (CONFIRMATION_LIMITS[channel] if state == "provider_accepted" else None),
    }


def _record_delivery(note_id: str | None, record: dict) -> None:
    if not note_id:
        return
    with session_scope() as session:
        row = session.get(Notification, note_id)
        if row is not None:
            row.delivery = record


def _classify(status: int | None = None, exc: Exception | None = None) -> tuple[str, bool]:
    """Why a provider request failed, and whether trying once more could help."""
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout", True
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
            return "unreachable", True
        return "request_error", False
    if status in (401, 403):
        return "rejected_credentials", False
    if status == 429:
        return "rate_limited", True
    if status is not None and status >= 500:
        return "provider_error", True
    return "rejected", False


def _blocked_reason(kind: str, recipient: str | None) -> str | None:
    """Why a real send must not happen to this recipient right now, or None when it may."""
    from app.runtime import runtime

    settings = runtime.settings
    if not settings.recipient_allowed(kind, recipient):
        return "blocked_not_allowlisted"
    if settings.notify_max_per_hour > 0:
        now = time.monotonic()
        with _budget_lock:
            window = _budget.setdefault(kind, deque())
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= settings.notify_max_per_hour:
                return "blocked_budget"
            window.append(now)
    return None


def reset_budget() -> None:
    with _budget_lock:
        _budget.clear()


def _message_id(response) -> str | None:
    """The provider's own reference for a message, when its answer has one."""
    try:
        body = response.json()
    except Exception:
        return None
    for holder in (body, body.get("data") if isinstance(body, dict) else None):
        if isinstance(holder, dict):
            for key in ("uid", "id", "message_id", "messageId", "name"):
                if holder.get(key):
                    return str(holder[key])[:80]
    return None


def send_push(user_id: str, case_id: str | None, title: str, body: str, data: dict | None = None) -> dict:
    from app.runtime import runtime

    began = time.perf_counter()
    note_id = _store(user_id, case_id, "push", title, body, data)
    settings = runtime.settings
    result = {"channel": "push", "in_app": "stored", "notification_id": note_id, "fcm": "skipped", "devices": 0}

    def finish(record: dict) -> dict:
        result["delivery"] = record
        result["latency_ms"] = int((time.perf_counter() - began) * 1000)
        _record_delivery(note_id, record)
        return result

    if settings.push_driver != "fcm":
        return finish(delivery("inbox_stored", "push", inbox=True, note="Stored in the PayShield inbox. No push provider is in use."))
    with session_scope() as session:
        devices = (
            session.query(Device)
            .filter(Device.user_id == user_id, Device.trusted.is_(True), Device.fcm_token.isnot(None))
            .all()
        )
        tokens = [device.fcm_token for device in devices if device.fcm_token]
    result["devices"] = len(tokens)
    if not tokens:
        result["fcm"] = "no_token"
        return finish(delivery("inbox_stored", "push", inbox=True, reason="no_device_token", note="Stored in the PayShield inbox. No registered phone has a push token."))
    try:
        access = _fcm_access_token(settings)
    except Exception as exc:
        log.warning("fcm auth failed: %s", exc)
        result["fcm"] = "failed"
        result["detail"] = "FCM authentication failed"
        return finish(delivery("failed", "push", inbox=True, provider="fcm", reason="auth_failed", retryable=False))
    sent, last_id, last_reason = 0, None, "rejected"
    for token in tokens:
        try:
            response = httpx.post(
                f"https://fcm.googleapis.com/v1/projects/{settings.firebase_project_id}/messages:send",
                headers={"Authorization": f"Bearer {access}"},
                json={
                    "message": {
                        "token": token,
                        "notification": {"title": title, "body": body},
                        "data": {k: str(v) for k, v in (data or {}).items()},
                    }
                },
                timeout=10,
            )
            if response.status_code < 300:
                sent += 1
                last_id = _message_id(response) or last_id
            else:
                log.warning("fcm send status %s", response.status_code)
                last_reason = _classify(response.status_code)[0]
        except Exception as exc:
            log.warning("fcm send failed: %s", exc)
            last_reason = _classify(exc=exc)[0]
    result["fcm"] = "sent" if sent else "failed"
    result["sent"] = sent
    if sent:
        return finish(delivery("provider_accepted", "push", inbox=True, provider="fcm", message_id=last_id))
    return finish(delivery("failed", "push", inbox=True, provider="fcm", reason=last_reason, retryable=last_reason in {"timeout", "unreachable", "rate_limited", "provider_error"}))


def send_email(
    user_id: str,
    case_id: str | None,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> dict:
    from app import faults
    from app.runtime import runtime

    began = time.perf_counter()

    def finish(result: dict, record: dict) -> dict:
        result["delivery"] = record
        result["latency_ms"] = int((time.perf_counter() - began) * 1000)
        _record_delivery(result.get("notification_id"), record)
        return result

    if faults.is_on("email_down"):
        log.warning("email server failed (drill) for case %s", case_id)
        return finish({"channel": "email", "status": "failed", "driver": "smtp", "detail": "mail server unreachable"}, delivery("failed", "email", provider="smtp", reason="unreachable", retryable=True, note="A drill switched the mail server off."))
    with session_scope() as session:
        user = session.get(User, user_id)
        email = user.email if user else None
    files = [
        {"filename": name, "bytes": len(blob), "sha256": __import__("hashlib").sha256(blob).hexdigest()}
        for name, blob, _type in (attachments or [])
    ]
    note_id = _store(user_id, case_id, "email", subject, body, {"attachments": files} if files else None)
    settings = runtime.settings
    if not email:
        return finish({"channel": "email", "status": "no_address", "notification_id": note_id}, delivery("failed", "email", inbox=True, reason="no_address"))
    if settings.email_driver != "smtp":
        log.info("email logged for %s subject=%s attachments=%s", email, subject, [item["filename"] for item in files])
        return finish(
            {"channel": "email", "status": "logged", "driver": "log", "to": email, "notification_id": note_id, "attachments": files},
            delivery("logged_only", "email", inbox=True, note="Written to the server log and kept in the inbox. No email was sent."),
        )
    blocked = _blocked_reason("email", email)
    if blocked:
        log.info("email not sent (%s) to a recipient not cleared for real sending", blocked)
        return finish(
            {"channel": "email", "status": "logged", "driver": "log", "notification_id": note_id, "attachments": files},
            delivery(blocked, "email", inbox=True, note="A real mail server is configured, but this recipient is not cleared for real sending in this mode."),
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(body)
    for name, blob, kind in attachments or []:
        main, _sep, sub_type = kind.partition("/")
        message.add_attachment(blob, maintype=main, subtype=sub_type or "octet-stream", filename=name)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        log.warning("smtp rejected the credentials")
        return finish({"channel": "email", "status": "failed", "driver": "smtp", "notification_id": note_id}, delivery("failed", "email", inbox=True, provider="smtp", reason="rejected_credentials"))
    except smtplib.SMTPRecipientsRefused:
        return finish({"channel": "email", "status": "failed", "driver": "smtp", "notification_id": note_id}, delivery("failed", "email", inbox=True, provider="smtp", reason="recipient_refused"))
    except Exception as exc:
        log.warning("smtp failed: %s", type(exc).__name__)
        reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
        return finish({"channel": "email", "status": "failed", "driver": "smtp", "notification_id": note_id}, delivery("failed", "email", inbox=True, provider="smtp", reason=reason, retryable=True))
    return finish({"channel": "email", "status": "sent", "driver": "smtp", "to": email, "notification_id": note_id}, delivery("provider_accepted", "email", inbox=True, provider="smtp"))


def send_sms(user_id: str, case_id: str | None, message: str) -> dict:
    from app.runtime import runtime

    assert_safe_sms(message)
    from app import faults

    began = time.perf_counter()

    def finish(result: dict, record: dict) -> dict:
        result["delivery"] = record
        result["latency_ms"] = int((time.perf_counter() - began) * 1000)
        _record_delivery(result.get("notification_id"), record)
        return result

    if faults.is_on("sms_down"):
        # Nothing was delivered, so nothing goes into the customer's inbox.
        log.warning("sms gateway failed (drill) for case %s", case_id)
        return finish({"channel": "sms", "status": "failed", "driver": "gateway", "detail": "gateway unreachable"}, delivery("failed", "sms", provider="textlk", reason="unreachable", retryable=True, note="A drill switched the SMS gateway off."))
    with session_scope() as session:
        user = session.get(User, user_id)
        phone = user.phone if user else None
    note_id = _store(user_id, case_id, "sms", "SMS", message, None)
    settings = runtime.settings
    if not phone:
        return finish({"channel": "sms", "status": "no_phone", "notification_id": note_id}, delivery("failed", "sms", inbox=True, reason="no_phone"))
    if settings.sms_driver != "textlk":
        log.info("sms logged for %s (%s chars)", phone, len(message))
        return finish(
            {"channel": "sms", "status": "logged", "driver": "log", "to": phone, "notification_id": note_id},
            delivery("logged_only", "sms", inbox=True, note="Written to the server log and kept in the inbox. No SMS was sent."),
        )
    blocked = _blocked_reason("sms", phone)
    if blocked:
        log.info("sms not sent (%s) to a recipient not cleared for real sending", blocked)
        return finish(
            {"channel": "sms", "status": "logged", "driver": "log", "notification_id": note_id},
            delivery(blocked, "sms", inbox=True, note="A real SMS gateway is configured, but this recipient is not cleared for real sending in this mode."),
        )
    recipient = phone.lstrip("+")
    try:
        response = httpx.post(
            settings.textlk_api_url,
            headers={
                "Authorization": f"Bearer {settings.textlk_api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "recipient": recipient,
                "sender_id": settings.sms_sender_id,
                "type": "plain",
                "message": message,
            },
            timeout=15,
        )
    except Exception as exc:
        log.warning("text.lk request failed: %s", type(exc).__name__)
        reason, retryable = _classify(exc=exc)
        return finish({"channel": "sms", "status": "failed", "driver": "textlk", "notification_id": note_id}, delivery("failed", "sms", inbox=True, provider="textlk", reason=reason, retryable=retryable))
    if response.status_code >= 300:
        log.warning("text.lk status %s", response.status_code)
        reason, retryable = _classify(status=response.status_code)
        return finish(
            {"channel": "sms", "status": "failed", "driver": "textlk", "http_status": response.status_code, "notification_id": note_id},
            delivery("failed", "sms", inbox=True, provider="textlk", reason=reason, retryable=retryable),
        )
    return finish({"channel": "sms", "status": "sent", "driver": "textlk", "to": phone, "notification_id": note_id}, delivery("provider_accepted", "sms", inbox=True, provider="textlk", message_id=_message_id(response)))
