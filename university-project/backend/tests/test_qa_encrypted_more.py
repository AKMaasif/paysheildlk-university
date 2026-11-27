"""More personal data encrypted at rest (telecom event numbers, notification text, report text), changing the key
without losing data, and a clean refusal for an oversized chunked upload."""
import os
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import crypto, limits, migrate, rekey
from app.agents.runner import begin_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Notification, Report, SimEvent, User
from app.runtime import init_runtime, runtime

NEW_KEY = "a-brand-new-key-0123456789-0123456789-zz"


def raw(sql):
    with session_scope() as session:
        return session.execute(text(sql)).all()


def run_a_case(customer="cus_nimal", scenario="sim_swap_transfer"):
    case_id = trigger(customer, scenario)
    begin_case(case_id)
    return case_id


# ------------------------------------------------------------------ what is encrypted

def test_telecom_event_numbers_are_encrypted_and_still_match_the_customer(boot):
    run_a_case()
    phones = [row[0] for row in raw("SELECT phone FROM sim_events")]
    assert phones and all(p.startswith("siv1$") for p in phones)
    with session_scope() as session:
        user = session.get(User, "cus_nimal")
        events = session.query(SimEvent).filter(SimEvent.phone == user.phone).all()
        assert events and all(e.phone == user.phone for e in events)  # the same number reads the same and matches the user's
        assert session.query(SimEvent).filter(SimEvent.phone == "+94000000000").count() == 0


def test_notification_text_is_encrypted_at_rest_but_titles_and_reading_are_unchanged(boot):
    run_a_case()
    bodies = [row[0] for row in raw("SELECT body FROM notifications")]
    assert bodies and all(b.startswith("gcm2$") for b in bodies)
    titles = [row[0] for row in raw("SELECT title FROM notifications")]
    assert all(not t.startswith("gcm2$") for t in titles)
    with session_scope() as session:
        notes = session.query(Notification).filter(Notification.user_id == "cus_nimal").all()
        assert notes and all(not n.body.startswith("gcm2$") and len(n.body) > 5 for n in notes)


def test_a_case_report_is_encrypted_at_rest_and_the_api_still_serves_it_readable(boot):
    case_id = run_a_case("cus_nimal", "port_out_critical")
    stored = [row[0] for row in raw("SELECT markdown FROM reports")]
    assert stored and all(m.startswith("gcm2$") and "Mobile" not in m for m in stored)
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with session_scope() as session:
            report_id = session.query(Report).filter(Report.case_id == case_id).first().id
        text_ = client.get(f"/api/reports/{report_id}/markdown", headers=headers).text
        assert "Mobile" in text_ and "gcm2$" not in text_
        assert client.get(f"/api/reports/{report_id}/pdf", headers=headers).status_code == 200


def test_text_that_merely_looks_like_ciphertext_is_returned_as_it_is(boot):
    with session_scope() as session:
        session.add(Notification(id="ntf_x", user_id="cus_nimal", case_id=None, channel="push", title="t", body="gcm2$not-really-encrypted", created_at=__import__("app.util", fromlist=["utcnow"]).utcnow()))
    with session_scope() as session:
        assert session.get(Notification, "ntf_x").body in {"gcm2$not-really-encrypted"}


def test_rows_left_in_plain_text_are_encrypted_at_the_next_start_and_read_the_same(boot):
    run_a_case()
    with session_scope() as session:
        user_phone = session.get(User, "cus_nimal").phone
        session.execute(text("UPDATE sim_events SET phone = :p"), {"p": user_phone})
        session.execute(text("UPDATE notifications SET body = 'plain note'"))
        session.execute(text("UPDATE reports SET markdown = 'plain report'"))
    init_runtime()
    assert all(r[0].startswith("siv1$") for r in raw("SELECT phone FROM sim_events"))
    assert all(r[0].startswith("gcm2$") for r in raw("SELECT body FROM notifications"))
    with session_scope() as session:
        assert {n.body for n in session.query(Notification).all()} == {"plain note"}
        assert {r.markdown for r in session.query(Report).all()} <= {"plain report"}


def test_migration_0005_round_trips_the_data_down_and_up(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    assert migrate.upgrade(engine) == "created"
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO sim_events (id, phone, event_type, occurred_at, origin) VALUES ('s1', '+94771234567', 'sim_swap', '2026-01-01', 'seed')"))
        connection.execute(text("INSERT INTO notifications (id, user_id, channel, title, body, created_at) VALUES ('n1', 'u', 'sms', 't', 'hello there', '2026-01-01')"))
        connection.execute(text("INSERT INTO reports (id, case_id, title, markdown, created_at) VALUES ('r1', 'c', 't', '# report', '2026-01-01')"))
    with engine.begin() as connection:
        command.upgrade(migrate._config(connection), "head")  # a no-op: the rows above are plain, so run the data step again by cycling
        command.downgrade(migrate._config(connection), "0004")
    with engine.begin() as connection:
        command.upgrade(migrate._config(connection), "0005")
    with engine.connect() as connection:
        phone, body, md = connection.execute(text("SELECT (SELECT phone FROM sim_events), (SELECT body FROM notifications), (SELECT markdown FROM reports)")).one()
    from app.config import Settings

    key = Settings.from_env().encryption_key
    assert phone.startswith("siv1$") and crypto.open_field(phone, key, "phone") == "+94771234567"
    assert body.startswith("gcm2$") and crypto.decrypt(body, key, "notification") == "hello there"
    assert md.startswith("gcm2$") and crypto.decrypt(md, key, "report") == "# report"


# ------------------------------------------------------------------ changing the key

def test_rotating_the_key_keeps_every_value_readable_with_the_new_key_only(boot, monkeypatch):
    run_a_case()
    old_key = runtime.settings.encryption_key
    with session_scope() as session:
        before = {u.id: (u.email, u.phone, u.nic, u.account_number) for u in session.query(User).all()}
        notes = {n.id: n.body for n in session.query(Notification).all()}
    counts = rekey.rotate(runtime.engine, old_key, NEW_KEY)
    assert counts["users.email"] == len(before) and counts["notifications.body"] == len(notes) and counts["sim_events.phone"] > 0
    assert counts["users.totp_secret"] >= 1 and counts.get("users.face_ref", 0) >= 0
    monkeypatch.setenv("ENCRYPTION_KEY", NEW_KEY)
    init_runtime()  # the start-up check passes with the new key
    with session_scope() as session:
        assert {u.id: (u.email, u.phone, u.nic, u.account_number) for u in session.query(User).all()} == before
        assert {n.id: n.body for n in session.query(Notification).all()} == notes
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "operator"}).status_code == 200
    monkeypatch.delenv("ENCRYPTION_KEY")
    with pytest.raises(crypto.WeakKeyError, match="does not match"):
        init_runtime()  # the old key no longer fits the data
    monkeypatch.setenv("ENCRYPTION_KEY", NEW_KEY)
    init_runtime()


def test_a_dry_run_and_a_wrong_old_key_change_nothing(boot):
    run_a_case()
    snapshot = raw("SELECT email, phone FROM users ORDER BY id")
    notes = raw("SELECT body FROM notifications ORDER BY id")
    assert rekey.rotate(runtime.engine, runtime.settings.encryption_key, NEW_KEY, dry_run=True)["users.email"] > 0
    assert raw("SELECT email, phone FROM users ORDER BY id") == snapshot and raw("SELECT body FROM notifications ORDER BY id") == notes
    with pytest.raises(rekey.RekeyError, match="old key"):
        rekey.rotate(runtime.engine, "not-the-key-0123456789-0123456789-abcdef", NEW_KEY)
    assert raw("SELECT email, phone FROM users ORDER BY id") == snapshot


def test_rotation_refuses_a_short_new_key_or_the_same_key(boot):
    old = runtime.settings.encryption_key
    with pytest.raises(crypto.WeakKeyError):
        rekey.rotate(runtime.engine, old, "short")
    with pytest.raises(rekey.RekeyError, match="same"):
        rekey.rotate(runtime.engine, old, old)


def test_the_command_line_reads_keys_from_the_environment_and_reports_without_printing_them(boot, monkeypatch, capsys):
    monkeypatch.setenv("OLD_ENCRYPTION_KEY", runtime.settings.encryption_key)
    monkeypatch.setenv("NEW_ENCRYPTION_KEY", NEW_KEY)
    monkeypatch.setenv("DATABASE_URL", str(runtime.engine.url.render_as_string(hide_password=False)))
    monkeypatch.setattr("sys.argv", ["app.rekey", "--dry-run"])
    assert rekey.main() == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and NEW_KEY not in out and runtime.settings.encryption_key not in out
    monkeypatch.delenv("NEW_ENCRYPTION_KEY")
    assert rekey.main() == 2


# ------------------------------------------------------------------ chunked uploads

def test_an_oversized_chunked_upload_gets_a_clear_413(boot):
    def chunks():
        for _ in range(limits.MAX_BODY_BYTES // (1024 * 1024) + 2):
            yield b"x" * (1024 * 1024)

    with TestClient(app) as client:
        reply = client.post("/api/auth/login", content=chunks(), headers={"content-type": "application/json"})
        assert reply.status_code == 413 and "too large" in reply.json()["detail"]
        assert client.get("/api/health").status_code == 200


def test_a_chunked_upload_under_the_limit_is_untouched(boot):
    def chunks():
        yield b'{"email": "analyst@payshield.lk", '
        yield b'"password": "pilot-analyst", "portal": "operator"}'

    with TestClient(app) as client:
        assert client.post("/api/auth/login", content=chunks(), headers={"content-type": "application/json"}).status_code == 200
    assert Path and os  # (kept for the file-level imports used above)


def test_the_start_refuses_when_only_an_authenticator_secret_was_written_with_another_key(boot):
    """An older database upgraded under a new key: the personal fields were encrypted under the new key by the
    migration, but an older-format authenticator secret still needs the key it was written with."""
    import hashlib

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = b"\x02" * 12
    old_key = hashlib.sha256(b"the-key-this-database-was-first-written-with").digest()
    legacy = "gcm$" + nonce.hex() + "$" + AESGCM(old_key).encrypt(nonce, b"JBSWY3DPEHPK3PXP", b"payshield").hex()
    with session_scope() as session:
        session.execute(text("UPDATE users SET totp_secret = :v WHERE totp_secret IS NOT NULL"), {"v": legacy})  # a real mismatch affects every row
    with pytest.raises(crypto.WeakKeyError, match="users.totp_secret"):
        init_runtime()
