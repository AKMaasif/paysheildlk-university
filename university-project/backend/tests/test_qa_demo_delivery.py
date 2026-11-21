"""Demonstration modes, recipient safety, what is truly known about each message, bounded retries, and tool traces."""
import smtplib
from types import SimpleNamespace

import httpx
import pytest

from app import faults, notify
from app.agents import tools
from app.agents.runner import begin_case, resume_case
from app.config import MODES, Settings
from app.db import session_scope
from app.demo import trigger
from app.models import Case, CaseEvent, Notification
from app.runtime import runtime

CREDENTIALS = {"TEXTLK_API_KEY": "sms-key", "SMTP_HOST": "smtp.example", "GEMINI_API_KEY": "gem-key", "META_API_KEY": "meta-key", "TELCO_BASE_URL": "https://operator.example/camara", "FIREBASE_PROJECT_ID": "proj"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    faults.clear_all()
    notify.reset_budget()
    for name in list(CREDENTIALS) + ["DEMO_MODE", "DEMO_ALLOWED_PHONES", "DEMO_ALLOWED_EMAILS", "NOTIFY_MAX_PER_HOUR", "NOTIFY_MAX_ATTEMPTS", "APP_ENV"]:
        monkeypatch.delenv(name, raising=False)
    yield
    faults.clear_all()
    notify.reset_budget()


def settings_for(monkeypatch, mode=None, **extra):
    for key, value in {**CREDENTIALS, **extra}.items():
        monkeypatch.setenv(key, value)
    if mode:
        monkeypatch.setenv("DEMO_MODE", mode)
    return Settings.from_env()


# ------------------------------------------------------------------ the modes

def test_the_default_mode_ignores_every_external_credential_and_says_which(monkeypatch):
    s = settings_for(monkeypatch)
    assert s.demo_mode == "deterministic"
    assert (s.sms_driver, s.email_driver, s.push_driver, s.telco_base_url, s.gemini_api_key, s.meta_api_key) == ("log", "log", "websocket", "", "", "")
    assert set(s.ignored_by_mode) == {"SMS (Text.lk)", "email (SMTP)", "push (Firebase)", "AI model (Gemini)", "AI model (Meta)", "telecom gateway"}


def test_hybrid_uses_real_messaging_and_models_but_never_the_telecom_gateway(monkeypatch):
    s = settings_for(monkeypatch, "hybrid")
    assert (s.sms_driver, s.email_driver, s.gemini_api_key, s.telco_base_url) == ("textlk", "smtp", "gem-key", "")
    assert s.ignored_by_mode == ("telecom gateway",)


@pytest.mark.parametrize("mode", ["external", "production"])
def test_external_and_production_use_everything_that_is_configured(monkeypatch, mode):
    s = settings_for(monkeypatch, mode)
    assert (s.sms_driver, s.email_driver, s.telco_base_url) == ("textlk", "smtp", "https://operator.example/camara") and s.ignored_by_mode == ()


def test_an_unknown_mode_is_refused_with_the_choices(monkeypatch):
    with pytest.raises(RuntimeError, match="deterministic, hybrid, external, production"):
        settings_for(monkeypatch, "live")


def test_production_must_not_run_in_a_demonstration_mode(monkeypatch):
    base = dict(APP_ENV="production", JWT_SECRET="j" * 40, ENCRYPTION_KEY="e" * 40, HMAC_KEY="h" * 40, ANSWER_PEPPER="a" * 40, DEMO_ASSIST="false", ALLOWED_ORIGINS="https://bank.example")
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    Settings.from_env().assert_safe_for_environment()  # APP_ENV=production alone means mode production
    monkeypatch.setenv("DEMO_MODE", "hybrid")
    with pytest.raises(RuntimeError, match="DEMO_MODE"):
        Settings.from_env().assert_safe_for_environment()


def test_every_mode_has_a_plain_sentence_for_the_presenter():
    from app.config import MODE_NOTES

    assert set(MODE_NOTES) == set(MODES) and all(len(note) > 40 for note in MODE_NOTES.values())


# ------------------------------------------------------------------ who may receive a real message

def test_in_the_demonstration_modes_only_listed_recipients_may_receive_real_messages(monkeypatch):
    s = settings_for(monkeypatch, "hybrid", DEMO_ALLOWED_PHONES="+94771111111, 0772222222", DEMO_ALLOWED_EMAILS="Tester@Example.com")
    assert s.recipient_allowed("sms", "+94771111111") and s.recipient_allowed("sms", "077 111 1111") and s.recipient_allowed("sms", "+94772222222")
    assert not s.recipient_allowed("sms", "+94773333333") and not s.recipient_allowed("sms", None) and not s.recipient_allowed("sms", "")
    assert s.recipient_allowed("email", "tester@example.com") and not s.recipient_allowed("email", "someone@else.lk")


def test_an_empty_allow_list_sends_to_nobody_in_the_demonstration_modes_but_does_not_apply_elsewhere(monkeypatch):
    assert not settings_for(monkeypatch, "external").recipient_allowed("sms", "+94771111111")
    assert settings_for(monkeypatch, "production").recipient_allowed("sms", "+94771111111")
    assert settings_for(monkeypatch, "deterministic").recipient_allowed("email", "anyone@anywhere.lk")  # nothing real can be sent in this mode anyway


# ------------------------------------------------------------------ what each sender reports

def real_sms(monkeypatch, response=None, exc=None, mode="production"):
    monkeypatch.setattr(runtime.settings, "textlk_api_key", "KEY")
    monkeypatch.setattr(runtime.settings, "demo_mode", mode)
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if exc:
            raise exc
        return response

    monkeypatch.setattr(httpx, "post", post)
    return calls


def reply(status=200, body=None):
    return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("POST", "https://x"))


def sms(case_id=None):
    return notify.send_sms("cus_nimal", case_id, notify.GENERIC_SMS)


def test_the_log_driver_says_logged_only_and_keeps_the_inbox_copy(boot):
    for send in (lambda: sms(), lambda: notify.send_email("cus_nimal", None, "Subject", "Body")):
        out = send()
        assert out["delivery"]["state"] == "logged_only" and out["delivery"]["inbox_stored"] is True and out["delivery"]["confirmed"] is False
    assert notify.send_push("cus_nimal", None, "T", "B")["delivery"]["state"] == "inbox_stored"


def test_a_provider_acceptance_is_accepted_not_delivered_and_carries_the_providers_id(boot, monkeypatch):
    real_sms(monkeypatch, reply(200, {"status": "success", "data": {"uid": "msg-123"}}))
    out = sms()
    assert out["delivery"]["state"] == "provider_accepted" and out["delivery"]["provider_message_id"] == "msg-123" and out["delivery"]["confirmed"] is False
    assert "not reported back" in out["delivery"]["note"] and out["status"] == "sent"


def test_no_integration_ever_reports_delivered(boot, monkeypatch):
    real_sms(monkeypatch, reply(200, {"status": "success", "delivered": True}))
    assert sms()["delivery"]["state"] != "delivered"


@pytest.mark.parametrize("status,reason,retryable", [(401, "rejected_credentials", False), (403, "rejected_credentials", False), (429, "rate_limited", True),
                                                      (500, "provider_error", True), (503, "provider_error", True), (400, "rejected", False)])
def test_each_provider_refusal_is_named_and_marked_retryable_only_when_a_retry_could_help(boot, monkeypatch, status, reason, retryable):
    real_sms(monkeypatch, reply(status))
    out = sms()
    assert out["status"] == "failed" and out["delivery"]["reason"] == reason and out["delivery"]["retryable"] is retryable and out["delivery"]["state"] == "failed"


@pytest.mark.parametrize("exc,reason", [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "unreachable")])
def test_a_provider_that_is_slow_or_down_is_a_retryable_failure(boot, monkeypatch, exc, reason):
    real_sms(monkeypatch, exc=exc)
    out = sms()
    assert out["delivery"]["reason"] == reason and out["delivery"]["retryable"] is True


def test_an_sms_to_someone_not_on_the_allow_list_never_reaches_the_provider(boot, monkeypatch):
    calls = real_sms(monkeypatch, reply(200), mode="hybrid")
    out = sms()
    assert calls == [] and out["delivery"]["state"] == "blocked_not_allowlisted" and out["delivery"]["inbox_stored"] is True and out["status"] == "logged"


def test_an_sms_to_a_listed_recipient_does_go_to_the_provider_in_hybrid_mode(boot, monkeypatch):
    calls = real_sms(monkeypatch, reply(200, {"data": {"id": "z9"}}), mode="hybrid")
    with session_scope() as session:
        from app.models import User

        phone = session.get(User, "cus_nimal").phone
    monkeypatch.setattr(runtime.settings, "demo_allowed_phones", (phone,))
    assert sms()["delivery"]["state"] == "provider_accepted" and len(calls) == 1


def test_the_hourly_limit_on_real_messages_is_enforced(boot, monkeypatch):
    calls = real_sms(monkeypatch, reply(200))
    monkeypatch.setattr(runtime.settings, "notify_max_per_hour", 3)
    states = [sms()["delivery"]["state"] for _ in range(5)]
    assert states == ["provider_accepted"] * 3 + ["blocked_budget"] * 2 and len(calls) == 3


def test_an_sms_with_a_link_or_a_code_is_refused_before_anything_is_sent(boot, monkeypatch):
    calls = real_sms(monkeypatch, reply(200))
    for bad in ("Open https://pay.example now", "Your code is 482913"):
        with pytest.raises(notify.UnsafeSms):
            notify.send_sms("cus_nimal", None, bad)
    assert calls == []


def test_a_missing_phone_or_address_is_a_failure_with_that_reason(boot):
    with session_scope() as session:
        from app.models import User

        user = session.get(User, "cus_nimal")
        user.phone, user.email = None, ""  # the column for the address cannot be empty of a value, but a blank one is no address
    assert sms()["delivery"]["reason"] == "no_phone"
    assert notify.send_email("cus_nimal", None, "S", "B")["delivery"]["reason"] == "no_address"


class SmtpStub:
    def __init__(self, fail=None):
        self.fail = fail
        self.sent = []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, message):
        if self.fail:
            raise self.fail
        self.sent.append(message)


def smtp(monkeypatch, stub, mode="production"):
    monkeypatch.setattr(runtime.settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(runtime.settings, "demo_mode", mode)
    monkeypatch.setattr(smtplib, "SMTP", stub)


def test_an_accepted_email_is_accepted_for_relay_and_not_confirmed(boot, monkeypatch):
    stub = SmtpStub()
    smtp(monkeypatch, stub)
    out = notify.send_email("cus_nimal", None, "S", "B")
    assert out["delivery"]["state"] == "provider_accepted" and out["delivery"]["confirmed"] is False and len(stub.sent) == 1 and "mailbox" in out["delivery"]["note"]


@pytest.mark.parametrize("exc,reason,retryable", [(smtplib.SMTPAuthenticationError(535, b"no"), "rejected_credentials", False), (smtplib.SMTPRecipientsRefused({}), "recipient_refused", False),
                                                    (TimeoutError("slow"), "timeout", True), (ConnectionRefusedError("down"), "unreachable", True)])
def test_each_mail_failure_is_named(boot, monkeypatch, exc, reason, retryable):
    smtp(monkeypatch, SmtpStub(fail=exc))
    out = notify.send_email("cus_nimal", None, "S", "B")
    assert out["delivery"]["reason"] == reason and out["delivery"]["retryable"] is retryable and out["status"] == "failed"


def test_an_email_to_someone_not_on_the_allow_list_never_reaches_the_mail_server(boot, monkeypatch):
    stub = SmtpStub()
    smtp(monkeypatch, stub, mode="external")
    out = notify.send_email("cus_nimal", None, "S", "B")
    assert stub.sent == [] and out["delivery"]["state"] == "blocked_not_allowlisted"


def test_the_delivery_record_is_stored_with_the_notification(boot, monkeypatch):
    real_sms(monkeypatch, reply(200, {"data": {"uid": "stored-1"}}))
    note_id = sms()["notification_id"]
    with session_scope() as session:
        assert session.get(Notification, note_id).delivery["provider_message_id"] == "stored-1"


def test_push_reports_a_firebase_acceptance_and_a_missing_token_separately(boot, monkeypatch):
    monkeypatch.setattr(runtime.settings, "firebase_project_id", "proj")
    monkeypatch.setattr(runtime.settings, "firebase_client_email", "a@b")
    monkeypatch.setattr(runtime.settings, "firebase_private_key", "k")
    assert notify.send_push("cus_nimal", None, "T", "B")["delivery"]["reason"] == "no_device_token"
    with session_scope() as session:
        from app.models import Device

        session.add(Device(id="dev-x", user_id="cus_nimal", label="p", platform="a", trusted=True, fcm_token="tok", created_at=notify.utcnow()))
    monkeypatch.setattr(notify, "_fcm_access_token", lambda s: "access")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: reply(200, {"name": "projects/proj/messages/0:1"}))
    out = notify.send_push("cus_nimal", None, "T", "B")
    assert out["delivery"]["state"] == "provider_accepted" and out["delivery"]["provider_message_id"].startswith("projects/proj")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: reply(500))
    assert notify.send_push("cus_nimal", None, "T", "B")["delivery"]["state"] == "failed"


# ------------------------------------------------------------------ bounded retries

def count_retries(case_id):
    with session_scope() as session:
        return session.query(CaseEvent).filter(CaseEvent.case_id == case_id, CaseEvent.phase == "retrying").count()


def test_a_timeout_is_retried_once_and_the_retry_is_visible(boot, monkeypatch):
    calls = real_sms(monkeypatch, exc=httpx.ReadTimeout("slow"))
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    out = tools.alert_customer("cus_nimal", case_id, 200_000, "K.D. Enterprises")["sms"]
    assert out["attempts"] == 2 and out["retried"] is True and len([c for c in calls if "message" in c[1]["json"]]) == 2 and count_retries(case_id) >= 1


def test_a_refused_credential_is_not_retried(boot, monkeypatch):
    calls = real_sms(monkeypatch, reply(401))
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    out = tools.alert_customer("cus_nimal", case_id, 200_000, "K.D. Enterprises")["sms"]
    assert out["attempts"] == 1 and len(calls) == 1 and "retried" not in out


def test_the_number_of_attempts_is_capped_by_the_setting(boot, monkeypatch):
    calls = real_sms(monkeypatch, exc=httpx.ReadTimeout("slow"))
    monkeypatch.setattr(runtime.settings, "notify_max_attempts", 1)
    out = tools.alert_customer("cus_nimal", trigger("cus_nimal", "sim_swap_transfer"), 200_000, "K.D. Enterprises")["sms"]
    assert out["attempts"] == 1 and len(calls) == 1


def test_a_missing_phone_is_not_retried(boot, monkeypatch):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    with session_scope() as session:
        from app.models import User

        session.get(User, "cus_nimal").phone = None
    out = tools.alert_customer("cus_nimal", case_id, 200_000, "x")["sms"]
    assert out["attempts"] == 1 and out["delivery"]["reason"] == "no_phone"


# ------------------------------------------------------------------ tool traces

def events_of(case_id):
    with session_scope() as session:
        rows = session.query(CaseEvent).filter(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at, CaseEvent.id).all()
        return [SimpleNamespace(**{c.name: getattr(r, c.name) for c in r.__table__.columns}) for r in rows]


@pytest.fixture()
def traced(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    return case_id


def test_every_tool_call_is_a_started_event_then_a_finished_event_with_one_call_id(traced):
    calls = {}
    for e in events_of(traced):
        if e.call_id:
            calls.setdefault(e.call_id, []).append(e)
    assert len(calls) >= 12
    for call_id, pair in calls.items():
        assert [e.phase for e in pair] == ["started", "finished"], call_id
        assert pair[0].tool_name == pair[1].tool_name and pair[0].created_at <= pair[1].created_at


def test_a_started_event_has_no_result_and_a_finished_event_has_a_duration_and_a_structured_result(traced):
    started = next(e for e in events_of(traced) if e.phase == "started")
    finished = next(e for e in events_of(traced) if e.phase == "finished" and e.call_id == started.call_id)
    assert started.result is None and started.result_summary is None and started.latency_ms is None
    assert finished.result and finished.latency_ms is not None and finished.result_summary


def test_every_event_of_a_run_carries_the_same_run_id_and_the_cases_identifiers(traced):
    first_run = [e for e in events_of(traced) if e.call_id]
    assert len({e.meta["run_id"] for e in first_run}) == 1 and first_run[0].meta["run_id"]
    assert {e.meta["case_id"] for e in first_run} == {traced} and all(e.meta["transaction_id"] for e in first_run)


def test_each_resume_gets_its_own_run_id(traced):
    before = {e.meta["run_id"] for e in events_of(traced) if e.meta}
    resume_case(traced, {"decision": "no"}, "waiting_customer")
    after = {e.meta["run_id"] for e in events_of(traced) if e.meta}
    assert len(after - before) == 1


def test_core_reads_and_workflow_calls_say_who_chose_them(traced):
    planners = {e.tool_name: e.meta["planner"] for e in events_of(traced) if e.phase == "started"}
    assert planners["get_sim_swap_status"] == "core read" and planners["compute_risk_score"] == "workflow" and planners["hold_transaction"] == "workflow"


def test_an_extra_lookup_names_its_planner_and_why(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    extra = [e for e in events_of(case_id) if e.phase == "started" and "planner" in e.meta["planner"] and e.meta["planner"] != "core read"]
    for e in extra:
        assert e.meta["planner"] in {"rules planner", "model planner"} and e.meta["why"]


def test_a_tool_that_raises_is_recorded_as_failed_with_the_same_call_id(boot):
    from app.agents.graph import _tool

    case_id = trigger("cus_nimal", "sim_swap_transfer")

    def boom():
        raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError):
        _tool(case_id, "telecom", "get_sim_swap_status", {"phoneNumber": "x"}, boom)
    pair = [e for e in events_of(case_id) if e.tool_name == "get_sim_swap_status"]
    assert [e.phase for e in pair] == ["started", "failed"] and pair[0].call_id == pair[1].call_id and pair[1].result == {"error": "TimeoutError"}


def test_a_tool_outside_the_agents_charter_is_refused_and_recorded_as_such(boot):
    from app.agents.graph import _tool

    case_id = trigger("cus_nimal", "sim_swap_transfer")
    with pytest.raises(PermissionError):
        _tool(case_id, "telecom", "release_transaction", {}, lambda: {})
    refused = [e for e in events_of(case_id) if e.tool_name == "release_transaction"]
    assert len(refused) == 1 and refused[0].state == "error" and refused[0].meta["refused"] == "charter" and refused[0].phase is None


def test_a_stored_result_never_holds_a_phone_number_an_address_or_a_secret(traced):
    blob = str([e.result for e in events_of(traced) if e.result])
    with session_scope() as session:
        from app.models import User

        user = session.get(User, "cus_nimal")
        private = [user.phone, user.email, user.nic, user.account_number]
    for value in private:
        assert value not in blob, value
    assert "password" not in blob.lower() and "embedding" not in blob.lower()


def test_the_notification_events_show_each_channels_real_state(traced):
    done = {e.tool_name: e for e in events_of(traced) if e.phase in {"finished", "failed"} and e.tool_name in {"send_push", "send_email", "send_sms"}}
    assert done["send_sms"].result["delivery"]["state"] == "logged_only" and "logged only" in done["send_sms"].result_summary.lower()
    assert done["send_push"].result["delivery"]["state"] == "inbox_stored" and done["send_email"].result["delivery"]["state"] == "logged_only"


# ------------------------------------------------------------------ the SIM freeze

def test_the_freeze_result_says_it_is_a_recorded_simulation_and_no_operator_was_contacted(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    out = tools.request_sim_freeze("+94772451183", case_id, "customer said no")
    assert out["simulated"] is True and out["operator_contacted"] is False and out["integration"] == "none" and out["status"] == "request_recorded_simulated"


def test_nothing_the_customer_or_analyst_reads_claims_the_sim_was_frozen(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    resume_case(case_id, {"decision": "no"}, "waiting_customer")
    with session_scope() as session:
        texts = [n.body for n in session.query(Notification).filter(Notification.case_id == case_id).all()]
        texts += [e.result_summary or e.message or "" for e in session.query(CaseEvent).filter(CaseEvent.case_id == case_id).all()]
        from app.models import Report

        texts.append(session.query(Report).filter(Report.case_id == case_id).one().markdown)
    from app.report_pdf import _customer_steps

    with session_scope() as session:
        texts += _customer_steps(session.get(Case, case_id), True)
    joined = " ".join(texts).lower()
    for claim in ("sim frozen", "sim was frozen", "asked your mobile operator", "asked the operator", "operator froze"):
        assert claim not in joined, claim
    assert "simulated" in joined and "not been told" in joined or "operator not contacted" in joined or "no operator" in joined


# ------------------------------------------------------------------ what the presenter sees

def test_health_reports_the_mode_what_it_ignored_and_what_is_real_without_showing_any_secret(boot):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["mode"] == "deterministic" and body["mode_note"] and body["recipients_cleared"] == {"phones": 0, "emails": 0}
    assert body["real_actions"] == {"bank_ledger": "synthetic", "sim_freeze": "simulated request, no operator integration", "sim_swap_data": "synthetic", "sms": "logged only", "email": "logged only"}
    assert "KEY" not in str(body) and "password" not in str(body).lower()



def test_resetting_the_demo_sends_no_message_to_anyone(boot, monkeypatch):
    from app.demo import reset_demo

    sent = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError("a reset must not send")))
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a reset must not send")))
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    reset_demo()
    assert sent == []
    with session_scope() as session:
        assert session.query(Notification).count() == 0  # the reset clears them; it creates none
