"""The Alert agent's reach: push, email and SMS, each with every way it can succeed or fail, and the timing of reminders."""
import smtplib
from datetime import timedelta

import httpx
import pytest

from app import faults, notify
from app.agents import tools
from app.agents.runner import begin_case, escalate_case, process_timeouts, realert_case
from app.db import session_scope
from app.demo import trigger
from app.models import Case, Device, Notification, User
from app.notify import (
    BLOCKED_SMS,
    GENERIC_SMS,
    UnsafeSms,
    assert_safe_sms,
    send_email,
    send_push,
    send_sms,
)
from app.runtime import runtime
from app.util import utcnow


@pytest.fixture(autouse=True)
def _clean(boot):
    faults.clear_all()
    notify._fcm_token, notify._fcm_expiry = None, 0
    yield
    faults.clear_all()


def _notes(user_id, channel):
    with session_scope() as session:
        return [n.body for n in session.query(Notification).filter(Notification.user_id == user_id, Notification.channel == channel).all()]


# ---------------------------------------------------------------- the SMS rule: no link, no code

@pytest.mark.parametrize(
    "text",
    ["go to http://x.lk", "go to https://x.lk/a", "visit www.pay.lk", "HTTP://UPPER.LK", "Your code is 1234", "otp 12345678", "pin 482913 now", "ref 9999 please"],
)
def test_sms_with_a_link_or_a_code_is_refused(text):
    with pytest.raises(UnsafeSms):
        assert_safe_sms(text)


@pytest.mark.parametrize("text", ["Call 123 now", "Rs. 200,000 held", "order 12345678901 shipped", "Reference ABC12", GENERIC_SMS, BLOCKED_SMS, ""])
def test_ordinary_sms_text_is_allowed(text):
    assert_safe_sms(text)


def test_an_unsafe_sms_is_refused_before_anything_is_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: sent.append(a) or httpx.Response(200))
    with pytest.raises(UnsafeSms):
        send_sms("cus_nimal", None, "Your code is 482913")
    assert sent == [] and _notes("cus_nimal", "sms") == []


# ---------------------------------------------------------------- SMS drivers

def _textlk(monkeypatch, response=None, error=None):
    monkeypatch.setattr(runtime.settings, "textlk_api_key", "KEY123")
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        if error:
            raise error
        return response

    monkeypatch.setattr(httpx, "post", post)
    return seen


def test_the_sms_gateway_gets_the_number_without_a_plus_and_the_key_in_a_header(monkeypatch):
    seen = _textlk(monkeypatch, httpx.Response(200))
    result = send_sms("cus_nimal", "PS-1", GENERIC_SMS)
    assert result["status"] == "sent" and result["driver"] == "textlk"
    assert seen["json"]["recipient"] == "94772451183" and seen["json"]["message"] == GENERIC_SMS
    assert seen["headers"]["Authorization"] == "Bearer KEY123"


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_a_gateway_error_is_reported_not_hidden(monkeypatch, status):
    _textlk(monkeypatch, httpx.Response(status))
    result = send_sms("cus_nimal", None, GENERIC_SMS)
    assert result["status"] == "failed" and result["http_status"] == status


def test_a_gateway_that_cannot_be_reached_is_reported(monkeypatch):
    _textlk(monkeypatch, error=httpx.ConnectError("down"))
    assert send_sms("cus_nimal", None, GENERIC_SMS)["status"] == "failed"


def test_with_no_key_the_sms_is_logged_and_stored_and_with_no_phone_it_says_so():
    assert send_sms("cus_nimal", "PS-1", GENERIC_SMS)["status"] == "logged"
    assert _notes("cus_nimal", "sms") == [GENERIC_SMS]
    with session_scope() as session:
        session.get(User, "cus_kumari").phone = None
    assert send_sms("cus_kumari", None, GENERIC_SMS)["status"] == "no_phone"


def test_when_the_sms_gateway_is_down_nothing_is_recorded_as_sent():
    faults.turn_on("sms_down")
    assert send_sms("cus_nimal", None, GENERIC_SMS)["status"] == "failed"
    assert _notes("cus_nimal", "sms") == []


# ---------------------------------------------------------------- email

class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.started, self.logged_in, self.sent = host, port, False, None, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


def _smtp(monkeypatch, cls=FakeSMTP, user="mailer"):
    FakeSMTP.instances = []
    monkeypatch.setattr(runtime.settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(runtime.settings, "smtp_user", user)
    monkeypatch.setattr(runtime.settings, "smtp_password", "pw")
    monkeypatch.setattr(smtplib, "SMTP", cls)


def test_an_email_is_sent_over_tls_with_login_and_attachment(monkeypatch):
    _smtp(monkeypatch)
    result = send_email("cus_nimal", "PS-1", "Receipt", "Body text", attachments=[("r.pdf", b"%PDF-1.4 x", "application/pdf")])
    assert result["status"] == "sent" and result["to"] == "nimal.perera@mail.lk"
    smtp = FakeSMTP.instances[0]
    message = smtp.sent[0]
    assert smtp.started and smtp.logged_in == ("mailer", "pw")
    assert message["To"] == "nimal.perera@mail.lk" and message["Subject"] == "Receipt"
    assert [part.get_filename() for part in message.iter_attachments()] == ["r.pdf"]
    assert result["notification_id"]


def test_no_login_is_attempted_when_no_smtp_user_is_set(monkeypatch):
    _smtp(monkeypatch, user="")
    send_email("cus_nimal", None, "s", "b")
    assert FakeSMTP.instances[0].logged_in is None


def test_a_mail_server_error_is_reported(monkeypatch):
    class Broken(FakeSMTP):
        def send_message(self, message):
            raise smtplib.SMTPException("rejected")

    _smtp(monkeypatch, Broken)
    assert send_email("cus_nimal", None, "s", "b")["status"] == "failed"


def test_email_without_an_address_and_the_log_driver_and_the_drill():
    with session_scope() as session:
        session.get(User, "cus_kumari").email = ""
    assert send_email("cus_kumari", None, "s", "b")["status"] == "no_address"
    logged = send_email("cus_nimal", None, "s", "b", attachments=[("a.pdf", b"x", "application/pdf")])
    assert logged["status"] == "logged" and logged["attachments"][0]["filename"] == "a.pdf"
    faults.turn_on("email_down")
    assert send_email("cus_nimal", None, "s2", "b")["status"] == "failed"
    assert "s2" not in str(_notes("cus_nimal", "email"))


# ---------------------------------------------------------------- push

def _fcm(monkeypatch, tokens=("t1",), trusted=True, access="ACCESS", post=None):
    monkeypatch.setattr(runtime.settings, "firebase_project_id", "proj")
    monkeypatch.setattr(runtime.settings, "firebase_client_email", "svc@proj.iam")
    monkeypatch.setattr(runtime.settings, "firebase_private_key", "key")
    with session_scope() as session:
        for i, token in enumerate(tokens):
            session.add(Device(id=f"dev_t{i}", user_id="cus_nimal", label="p", fcm_token=token, trusted=trusted))
    monkeypatch.setattr(notify, "_fcm_access_token", (lambda s: access) if not isinstance(access, Exception) else (lambda s: (_ for _ in ()).throw(access)))
    calls = []
    monkeypatch.setattr(httpx, "post", post or (lambda url, headers=None, json=None, timeout=None: calls.append((url, json)) or httpx.Response(200)))
    return calls


def test_without_firebase_the_push_is_kept_in_the_app_inbox_only():
    result = send_push("cus_nimal", "PS-1", "Title", "Body")
    assert result["fcm"] == "skipped" and result["in_app"] == "stored" and _notes("cus_nimal", "push") == ["Body"]


def test_push_goes_to_each_trusted_device_with_a_string_payload(monkeypatch):
    calls = _fcm(monkeypatch, tokens=("t1", "t2"))
    result = send_push("cus_nimal", "PS-1", "Title", "Body", {"case_id": "PS-1", "n": 5})
    assert result["fcm"] == "sent" and result["sent"] == 2 and len(calls) == 2
    assert calls[0][1]["message"]["data"] == {"case_id": "PS-1", "n": "5"} and calls[0][0].endswith("/projects/proj/messages:send")


def test_an_untrusted_device_gets_no_push(monkeypatch):
    calls = _fcm(monkeypatch, trusted=False)
    assert send_push("cus_nimal", None, "T", "B")["fcm"] == "no_token" and calls == []


def test_push_reports_a_failed_login_to_firebase_and_failed_sends(monkeypatch):
    _fcm(monkeypatch, access=RuntimeError("auth"))
    assert send_push("cus_nimal", None, "T", "B")["fcm"] == "failed"


def test_push_counts_only_the_sends_that_worked(monkeypatch):
    answers = iter([httpx.Response(200), httpx.Response(500), RuntimeError("net")])

    def post(url, headers=None, json=None, timeout=None):
        item = next(answers)
        if isinstance(item, Exception):
            raise item
        return item

    _fcm(monkeypatch, tokens=("a", "b", "c"), post=post)
    result = send_push("cus_nimal", None, "T", "B")
    assert result["sent"] == 1 and result["fcm"] == "sent"


def test_if_every_push_fails_it_says_failed(monkeypatch):
    _fcm(monkeypatch, post=lambda *a, **k: httpx.Response(500))
    assert send_push("cus_nimal", None, "T", "B")["fcm"] == "failed"


def test_the_firebase_login_token_is_cached_until_it_nearly_expires(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setattr(runtime.settings, "firebase_client_email", "svc@proj.iam")
    monkeypatch.setattr(runtime.settings, "firebase_private_key", pem)
    posts = []
    monkeypatch.setattr(httpx, "post", lambda url, data=None, timeout=None: posts.append(data) or httpx.Response(200, json={"access_token": "TOK"}, request=httpx.Request("POST", url)))
    assert notify._fcm_access_token(runtime.settings) == "TOK"
    assert notify._fcm_access_token(runtime.settings) == "TOK" and len(posts) == 1
    notify._fcm_expiry = 0
    notify._fcm_access_token(runtime.settings)
    assert len(posts) == 2 and posts[0]["grant_type"].endswith("jwt-bearer")


# ---------------------------------------------------------------- what the customer is told

def test_the_alert_names_the_payment_and_the_sms_names_nothing_attacker_typed():
    tools.alert_customer("cus_nimal", "PS-1", 200_000, "Evil Ltd")
    push, sms, email = _notes("cus_nimal", "push")[0], _notes("cus_nimal", "sms")[0], _notes("cus_nimal", "email")[0]
    assert "Rs. 200,000" in push and "Evil Ltd" in push and "Rs. 200,000" in email
    assert "Evil" not in sms and "Rs." not in sms
    assert "We will not send a code by SMS" in email


def test_a_failed_email_or_sms_is_tried_once_more_and_the_retry_is_marked():
    faults.turn_on("email_down")
    faults.turn_on("sms_down")
    bundle = tools.alert_customer("cus_nimal", "PS-1", 5_000, "Shop")
    assert bundle["email"]["retried"] and bundle["sms"]["retried"] and bundle["push"]["in_app"] == "stored"


def test_every_alert_says_the_bank_never_asks_for_a_code():
    assert "never ask you to share a code" in GENERIC_SMS and "never ask you to share a code" in BLOCKED_SMS


# ---------------------------------------------------------------- reminders and escalation, to the second

def _held_case():
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def _age_case(case_id, seconds):
    then = utcnow() - timedelta(seconds=seconds)
    with session_scope() as session:
        case = session.get(Case, case_id)
        case.waiting_since, case.last_alert_at = then, then


@pytest.mark.parametrize("seconds,expect", [(60, (0, 0)), (179, (0, 0)), (181, (1, 0)), (599, (1, 0)), (601, (0, 1))])
def test_reminder_at_three_minutes_and_escalation_at_ten(seconds, expect):
    case_id = _held_case()
    _age_case(case_id, seconds)
    got = process_timeouts()
    assert (got["realert"], got["escalate"]) == expect


def test_a_reminder_is_sent_once_and_the_money_stays_held():
    case_id = _held_case()
    _age_case(case_id, 200)
    assert process_timeouts()["realert"] == 1
    with session_scope() as session:
        case = session.get(Case, case_id)
        assert case.alert_count == 2 and case.status == "waiting_customer"
    assert process_timeouts()["realert"] == 0
    assert len(_notes("cus_nimal", "sms")) >= 2


def test_reminders_and_escalation_do_nothing_for_a_case_that_is_not_waiting():
    case_id = trigger("cus_nimal", "routine_payment")
    begin_case(case_id)
    assert realert_case(case_id) is False and escalate_case(case_id) is False
    assert realert_case("PS-does-not-exist") is False


def test_when_every_channel_fails_the_case_is_still_reachable_in_the_app():
    faults.turn_on("sms_down")
    faults.turn_on("email_down")
    case_id = _held_case()
    assert "waiting" in _status(case_id)
    assert _notes("cus_nimal", "push")


def _status(case_id):
    with session_scope() as session:
        return session.get(Case, case_id).status
