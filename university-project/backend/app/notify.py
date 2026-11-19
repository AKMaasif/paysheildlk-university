import logging
import re
import smtplib
import time
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


def send_push(user_id: str, case_id: str | None, title: str, body: str, data: dict | None = None) -> dict:
    from app.runtime import runtime

    note_id = _store(user_id, case_id, "push", title, body, data)
    settings = runtime.settings
    result = {"channel": "push", "in_app": "stored", "notification_id": note_id, "fcm": "skipped", "devices": 0}
    if settings.push_driver != "fcm":
        return result
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
        return result
    try:
        access = _fcm_access_token(settings)
    except Exception as exc:
        log.warning("fcm auth failed: %s", exc)
        result["fcm"] = "failed"
        result["detail"] = "FCM authentication failed"
        return result
    sent = 0
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
            else:
                log.warning("fcm send status %s", response.status_code)
        except Exception as exc:
            log.warning("fcm send failed: %s", exc)
    result["fcm"] = "sent" if sent else "failed"
    result["sent"] = sent
    return result


def send_email(
    user_id: str,
    case_id: str | None,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> dict:
    from app import faults
    from app.runtime import runtime

    if faults.is_on("email_down"):
        log.warning("email server failed (drill) for case %s", case_id)
        return {"channel": "email", "status": "failed", "driver": "smtp", "detail": "mail server unreachable"}
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
        return {"channel": "email", "status": "no_address", "notification_id": note_id}
    if settings.email_driver != "smtp":
        log.info("email logged for %s subject=%s attachments=%s", email, subject, [item["filename"] for item in files])
        return {"channel": "email", "status": "logged", "driver": "log", "to": email, "notification_id": note_id, "attachments": files}
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
    except Exception as exc:
        log.warning("smtp failed: %s", exc)
        return {"channel": "email", "status": "failed", "driver": "smtp", "notification_id": note_id}
    return {"channel": "email", "status": "sent", "driver": "smtp", "to": email, "notification_id": note_id}


def send_sms(user_id: str, case_id: str | None, message: str) -> dict:
    from app.runtime import runtime

    assert_safe_sms(message)
    from app import faults

    if faults.is_on("sms_down"):
        # Nothing was delivered, so nothing goes into the customer's inbox.
        log.warning("sms gateway failed (drill) for case %s", case_id)
        return {"channel": "sms", "status": "failed", "driver": "gateway", "detail": "gateway unreachable"}
    with session_scope() as session:
        user = session.get(User, user_id)
        phone = user.phone if user else None
    note_id = _store(user_id, case_id, "sms", "SMS", message, None)
    settings = runtime.settings
    if not phone:
        return {"channel": "sms", "status": "no_phone", "notification_id": note_id}
    if settings.sms_driver != "textlk":
        log.info("sms logged for %s (%s chars)", phone, len(message))
        return {
            "channel": "sms",
            "status": "logged",
            "driver": "log",
            "to": phone,
            "notification_id": note_id,
        }
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
        log.warning("text.lk request failed: %s", exc)
        return {"channel": "sms", "status": "failed", "driver": "textlk", "notification_id": note_id}
    if response.status_code >= 300:
        log.warning("text.lk status %s", response.status_code)
        return {
            "channel": "sms",
            "status": "failed",
            "driver": "textlk",
            "http_status": response.status_code,
            "notification_id": note_id,
        }
    return {"channel": "sms", "status": "sent", "driver": "textlk", "to": phone, "notification_id": note_id}
