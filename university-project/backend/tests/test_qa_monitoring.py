"""Logs without secrets, request ids, readiness, and the numbers an operator would alert on."""
import io
import json
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import faults, logs, ops, ratelimit
from app.agents.runner import begin_case, timeout_loop
from app.demo import trigger
from app.main import app
from app.runtime import runtime
from app.security import create_token

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c3JfYWRtaW4ifQ.signaturesignature123"


@pytest.fixture(autouse=True)
def _clean():
    ops.reset()
    faults.clear_all()
    yield
    faults.clear_all()


def headers(user_id="usr_analyst", role="fraud_analyst"):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


# ------------------------------------------------------------------ no secrets in a log line

@pytest.mark.parametrize("text,gone", [
    (f"GET /ws?token={TOKEN} HTTP/1.1", TOKEN), ("/x?access_token=abcdef123456&y=1", "abcdef123456"), ("POST /a?password=hunter2hunter2", "hunter2"),
    ("calling with Authorization: Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"), ("/p?api_key=KEY123456789&q=1", "KEY123456789"), ("/p?code=482913", "482913"),
])
def test_secret_looking_values_are_blanked(text, gone):
    out = logs.scrub(text)
    assert gone not in out and "[removed]" in out


def test_ordinary_text_is_left_alone():
    plain = "case PS-26-00046 held; payee K.D. Enterprises; Rs. 200,000; tokens issued: 3"
    assert logs.scrub(plain) == plain


def test_the_server_access_log_never_prints_the_live_stream_token():
    logs.configure_logging()
    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    logger = logging.getLogger("uvicorn.access")
    logger.addHandler(handler)
    try:
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", f"/ws?token={TOKEN}", "1.1", 200)
    finally:
        logger.removeHandler(handler)
    assert TOKEN not in captured.getvalue() and "/ws?token=[removed]" in captured.getvalue()


def test_an_exception_message_is_scrubbed_too():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed for %s", (f"/ws?token={TOKEN}",), None)
    logs.RedactSecrets().filter(record)
    assert TOKEN not in record.getMessage()


# ------------------------------------------------------------------ JSON lines

def test_json_lines_are_one_object_with_the_expected_fields_and_no_secrets():
    record = logging.LogRecord("payshield.http", logging.WARNING, __file__, 1, "slow request %s", (f"/ws?token={TOKEN}",), None)
    record.request_id, record.method, record.path, record.status, record.duration_ms = "abc12345", "GET", "/api/cases", 200, 2500
    line = logs.JsonFormatter().format(record)
    data = json.loads(line)
    assert "\n" not in line and TOKEN not in line
    assert data["level"] == "WARNING" and data["logger"] == "payshield.http" and data["request_id"] == "abc12345" and data["duration_ms"] == 2500
    assert data["time"].endswith("Z")


def test_an_exception_is_included_in_a_json_line():
    try:
        raise ValueError("boom with token=SECRETSECRET")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    data = json.loads(logs.JsonFormatter().format(record))
    assert "ValueError" in data["exception"] and "SECRETSECRET" not in data["exception"]


def test_log_format_is_chosen_by_the_environment_and_setting_it_twice_adds_no_duplicate_handler(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setenv("LOG_FORMAT", "json")
    logs.configure_logging()
    logs.configure_logging()
    ours = [h for h in root.handlers if getattr(h, "_payshield", False)]
    assert len(ours) == 1 and isinstance(ours[0].formatter, logs.JsonFormatter)
    monkeypatch.setenv("LOG_FORMAT", "text")
    logs.configure_logging()
    ours = [h for h in root.handlers if getattr(h, "_payshield", False)]
    assert len(ours) == 1 and not isinstance(ours[0].formatter, logs.JsonFormatter)


# ------------------------------------------------------------------ request ids

def test_every_response_carries_a_request_id_and_a_sane_caller_id_is_kept(boot):
    with TestClient(app) as client:
        first, second = client.get("/api/health"), client.get("/api/health")
        assert first.headers["x-request-id"] != second.headers["x-request-id"] and len(first.headers["x-request-id"]) >= 8
        assert client.get("/api/health", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
        for bad in ("short", "has spaces in it!!", "x" * 200, "<script>alert(1)</script>"):
            got = client.get("/api/health", headers={"X-Request-ID": bad}).headers["x-request-id"]
            assert got != bad and len(got) == 32


def test_a_server_error_is_logged_with_its_id_counted_and_never_shows_internals(boot, caplog):
    def explode():
        raise RuntimeError("secret internal detail")

    count = len(app.router.routes)
    app.add_api_route("/__test_explode", explode, methods=["GET"])
    try:
        with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.ERROR, logger="payshield.http"):
            reply = client.get("/__test_explode", headers={"X-Request-ID": "trace-failure-001"})
    finally:
        del app.router.routes[count:]
    assert reply.status_code == 500 and "secret internal detail" not in reply.text
    assert any(getattr(r, "request_id", "") == "trace-failure-001" and r.levelno >= logging.ERROR for r in caplog.records)
    assert {"name": "http_5xx", "labels": {}, "value": 1} in ops.counters()


# ------------------------------------------------------------------ readiness

def test_ready_is_public_and_reports_each_check(boot):
    with TestClient(app) as client:
        reply = client.get("/api/ready")
        assert reply.status_code == 200 and reply.json()["ready"] is True
        assert {"database", "checkpointer", "timers"} <= set(reply.json()["checks"]) and set(reply.json()["checks"].values()) == {"ok"}


def test_ready_says_503_when_the_database_cannot_be_reached(boot, monkeypatch):
    with TestClient(app) as client:
        def down():
            raise RuntimeError("connection refused: postgresql://user:password@host/db")

        monkeypatch.setattr(runtime.engine, "connect", down)
        reply = client.get("/api/ready")
        assert reply.status_code == 503 and reply.json()["checks"]["database"] == "down"
        assert "password" not in reply.text and "postgresql" not in reply.text


def test_ready_says_503_when_the_reminder_and_escalation_loop_has_stopped(boot):
    with TestClient(app) as client:
        runtime.last_sweep = time.time() - 120
        reply = client.get("/api/ready")
        assert reply.status_code == 503 and reply.json()["checks"]["timers"] == "stalled"


def test_a_missing_redis_is_degraded_not_down(boot, monkeypatch):
    import dataclasses

    with TestClient(app) as client:
        monkeypatch.setattr(runtime, "settings", dataclasses.replace(runtime.settings, redis_url="redis://nowhere:6379/0"))
        monkeypatch.setattr(runtime, "redis", None)
        reply = client.get("/api/ready")
        assert reply.status_code == 200 and reply.json()["checks"]["redis"] == "degraded"


def test_the_timer_loop_leaves_a_heartbeat(boot):
    runtime.last_sweep = 0.0
    stop = threading.Event()
    thread = threading.Thread(target=timeout_loop, args=(stop, None), daemon=True)
    thread.start()
    time.sleep(2.6)
    stop.set()
    thread.join(timeout=5)
    assert time.time() - runtime.last_sweep < 5


# ------------------------------------------------------------------ the numbers

def test_metrics_are_for_signed_in_operators_only(boot):
    with TestClient(app) as client:
        assert client.get("/api/ops/metrics").status_code == 401
        assert client.get("/api/ops/metrics", headers=headers("cus_nimal", "customer")).status_code == 403
        assert client.get("/api/ops/metrics", headers=headers("usr_auditor", "auditor")).status_code == 200


def test_the_gauges_follow_the_ledger(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    with TestClient(app) as client:
        g = client.get("/api/ops/metrics", headers=headers()).json()["gauges"]
    assert g["held_payments"] >= 1 and g["cases_by_status"].get("waiting_customer", 0) >= 1
    assert g["oldest_waiting_customer_seconds"] >= 0 and g["audit_chain_ok"] is True and g["uptime_seconds"] >= 0
    assert set(g) >= {"escalated_cases", "agent_errors_last_hour", "emergency_districts", "timer_sweep_age_seconds", "audit_entries"}


def metric(client, name, **labels):
    for item in client.get("/api/ops/metrics", headers=headers()).json()["counters"]:
        if item["name"] == name and item["labels"] == labels:
            return item["value"]
    return 0


def test_a_failing_gateway_shows_up_as_a_rising_notification_failure_count(boot):
    faults.turn_on("sms_down")
    faults.turn_on("email_down")
    begin_case(trigger("cus_nimal", "sim_swap_transfer"))
    with TestClient(app) as client:
        assert metric(client, "notification_failures", channel="sms") >= 1
        assert metric(client, "notification_failures", channel="email") >= 1
        assert metric(client, "notification_failures", channel="push") == 0


def test_a_crashing_agent_is_counted_and_the_fail_safe_is_counted(boot):
    faults.turn_on("fail_agent", "risk")
    begin_case(trigger("cus_nimal", "sim_swap_transfer"))
    with TestClient(app) as client:
        assert metric(client, "agent_errors", agent="risk") == 1
        assert metric(client, "fail_safe") >= 1
        g = client.get("/api/ops/metrics", headers=headers()).json()["gauges"]
        assert g["agent_errors_last_hour"] >= 1 and g["escalated_cases"] >= 1


def test_wrong_passwords_and_blocked_attempts_are_counted(boot):
    ratelimit.reset_all()
    with TestClient(app) as client:
        for _ in range(8):
            client.post("/api/auth/login", json={"email": "telco@payshield.lk", "password": "wrong-password-1", "portal": "operator"})
        assert metric(client, "rate_limit_attempts", bucket="login_fail") >= 5
        assert metric(client, "rate_limit_blocks", bucket="login_fail") >= 1


def test_the_prometheus_text_is_well_formed(boot):
    begin_case(trigger("cus_nimal", "sim_swap_transfer"))
    with TestClient(app) as client:
        reply = client.get("/api/ops/metrics?format=prometheus", headers=headers())
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/plain")
    lines = [line for line in reply.text.splitlines() if line and not line.startswith("#")]
    assert lines and all(len(line.rsplit(" ", 1)) == 2 and line.rsplit(" ", 1)[1].replace(".", "").isdigit() for line in lines), lines[:3]
    names = {line.split("{")[0].split(" ")[0] for line in lines}
    assert {"payshield_held_payments", "payshield_audit_chain_ok", "payshield_uptime_seconds", "payshield_timer_sweep_age_seconds", "payshield_cases"} <= names
    assert any(line.startswith('payshield_cases{status="waiting_customer"}') for line in lines)


# ------------------------------------------------------------------ against a real server process

@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_real_server_never_writes_the_live_stream_token_to_its_log(tmp_path, fmt):
    import os
    import socket
    import subprocess
    import sys

    import httpx
    import websockets.sync.client as ws_client

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log_file = tmp_path / "server.log"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/a.db", "CHECKPOINT_PATH": str(tmp_path / "c.sqlite"), "CHECKPOINTER": "sqlite", "REDIS_URL": "",
           "JWT_SECRET": "test-secret-not-for-production-use", "FACE_ENGINE": "legacy", "STEP_DELAY_MS": "0", "LOG_FORMAT": fmt, "GEMINI_API_KEY": "", "META_API_KEY": ""}
    with open(log_file, "w") as sink:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=env, stdout=sink, stderr=sink)
        try:
            for _ in range(120):
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/ready", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
            token = httpx.post(f"http://127.0.0.1:{port}/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}).json()["token"]
            with ws_client.connect(f"ws://127.0.0.1:{port}/ws?token={token}") as socket_:
                assert json.loads(socket_.recv())["type"] == "snapshot"
            time.sleep(0.5)
        finally:
            proc.terminate()
            proc.wait(timeout=15)
    text = log_file.read_text()
    assert "/ws" in text, text[-800:]
    assert token not in text and token[:40] not in text
    assert "token=[removed]" in text
    if fmt == "json":
        assert all(json.loads(line) for line in text.splitlines() if line.startswith("{"))


def test_a_live_stream_client_that_leaves_is_released_at_once_not_at_the_next_ping(boot):
    with TestClient(app) as client:
        before = len(runtime.bus.subscribers)
        with client.websocket_connect(f"/ws?token={create_token(runtime.settings, 'usr_analyst', 'fraud_analyst')}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            assert len(runtime.bus.subscribers) == before + 1
        for _ in range(40):
            if len(runtime.bus.subscribers) == before:
                break
            time.sleep(0.05)
        assert len(runtime.bus.subscribers) == before


def test_a_live_stream_client_still_receives_events_and_pings_are_not_needed_for_that(boot):
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={create_token(runtime.settings, 'usr_analyst', 'fraud_analyst')}") as ws:
            ws.receive_json()
            case_id = trigger("cus_nimal", "sim_swap_transfer")
            begin_case(case_id)
            seen = set()
            for _ in range(60):
                message = ws.receive_json()
                if message["type"] == "event":
                    seen.add(message["event"]["case_id"])
                if case_id in seen:
                    break
            assert case_id in seen
