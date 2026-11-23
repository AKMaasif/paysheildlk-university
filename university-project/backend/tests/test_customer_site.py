"""Registration, username sign-in, account summary and password change for the customer site."""
from fastapi.testclient import TestClient
from helpers import (
    PASSWORD,
    authenticate,
    correct_answers,
    frames,
    live_selfie,
    record,
    register,
    sign_in,
    username_for,
)

from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Case, Notification, Transaction, User
from app.security import verify_password


def _user(customer_id="cus_nimal"):
    with session_scope() as session:
        row = session.get(User, customer_id)
        session.expunge(row)
        return row


def test_register_claims_the_bank_record_and_stores_everything(boot):
    with TestClient(app) as client:
        reply = register(client)
        assert reply.status_code == 200, reply.text
        assert "token" not in reply.json() and reply.json()["step"] == "enrol" and reply.json()["pending_token"]
        assert reply.json()["user"]["bank"] == "BankCylone"
    row = _user()
    assert row.registered and row.registered_at is not None
    assert row.username == "nimal.perera"  # the one the test chose at registration
    assert verify_password(PASSWORD, row.password_hash) and PASSWORD not in row.password_hash
    assert row.face_ref.startswith("gcm2$") and len(row.face_hash) == 64
    with session_scope() as session:
        sent = [n.channel for n in session.query(Notification).filter(Notification.user_id == "cus_nimal").all()]
    assert "email" in sent and "sms" in sent


def test_every_field_must_match_the_record(boot):
    wrong = {
        "full_name": "Nimal Silva",
        "email": "someone@mail.lk",
        "nic": "198524701999",
        "phone": "+94771111111",
        "account_number": "8200451191",
    }
    with TestClient(app) as client:
        for field, value in wrong.items():
            reply = register(client, **{field: value})
            assert reply.status_code == 422, field
            # The answer never says which field was wrong.
            assert "do not match" in reply.json()["detail"]
        assert not _user().registered


def test_typing_differences_that_mean_the_same_thing_are_accepted(boot):
    row = record("cus_nimal")
    with TestClient(app) as client:
        reply = register(
            client,
            full_name="  NIMAL   perera ",
            email=row["email"].upper(),
            nic=row["nic"].lower(),
            phone="077 245 1183",
            account_number="8200 4511 90",
        )
    assert reply.status_code == 200, reply.text


def test_second_registration_is_refused(boot):
    with TestClient(app) as client:
        assert register(client).status_code == 200
        again = register(client, password="Another-9753", confirm_password="Another-9753")
    assert again.status_code == 409


def test_password_and_face_scan_rules(boot):
    with TestClient(app) as client:
        assert register(client, confirm_password="Harbour-0000").status_code == 422
        for weak in ("short1", "alllettersnodigits", "12345678", "nimalperera1"):
            reply = register(client, password=weak, confirm_password=weak)
            assert reply.status_code == 422, weak
        # A scan with no camera frames, or two identical frames (a photo), does not pass.
        assert register(client, frames=[]).status_code == 422
        still = frames()[0]
        assert register(client, frames=[still, still]).status_code == 422
        assert not _user().registered
        assert register(client).status_code == 200


def test_sign_in_needs_registration_then_only_username_and_password(boot):
    with TestClient(app) as client:
        before = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"})
        assert before.status_code == 401
        assert register(client).status_code == 200
        ok = client.post("/api/auth/login", json={"email": " Nimal.Perera ", "password": PASSWORD, "portal": "customer"})
        assert ok.status_code == 200 and "token" not in ok.json() and ok.json()["pending_token"]  # the code step follows
        bad = client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": "Wrong-1234", "portal": "customer"})
        assert bad.status_code == 401
        # A customer cannot use the operator console, and an operator cannot use the customer site.
        assert client.post("/api/auth/login", json={"email": record("cus_nimal")["email"], "password": PASSWORD, "portal": "operator"}).status_code == 403
        assert client.post("/api/auth/login", json={"email": "analyst@payshield.lk", "password": "pilot-analyst", "portal": "customer"}).status_code == 401


def test_account_summary_shows_bank_balance_and_history(boot):
    with TestClient(app) as client:
        register(client)
        headers = sign_in(client)
        info = client.get("/api/customer/account", headers=headers).json()
    assert info["bank"] == "BankCylone"
    assert info["balance_lkr"] == record("cus_nimal")["balance"]
    assert info["account_number"].endswith("1190") and "8200" not in info["account_number"]
    assert info["available_lkr"] == info["balance_lkr"]
    assert info["transactions"] and all(t["kind"] in {"Transfer", "Bill payment", "Card purchase", "Salary"} for t in info["transactions"])
    assert {t["direction"] for t in info["transactions"]} <= {"debit", "credit"}


def test_only_one_bank_name_exists(boot):
    with session_scope() as session:
        banks = {row[0] for row in session.query(User.bank).filter(User.bank.isnot(None)).all()}
        payee_banks = {row[0] for row in session.query(Transaction.payee_bank).filter(Transaction.payee_bank.isnot(None)).all()}
    assert banks == {"BankCylone"} and payee_banks <= {"BankCylone"}


def test_change_password_updates_database_and_sends_email_and_sms(boot):
    with TestClient(app) as client:
        register(client)
        headers = sign_in(client)
        body = {"old_password": PASSWORD, "new_password": "Lagoon-86420", "confirm_password": "Lagoon-86420"}
        assert client.post("/api/customer/password", json={**body, "old_password": "Not-the-one-1"}, headers=headers).status_code == 401
        assert client.post("/api/customer/password", json={**body, "confirm_password": "Lagoon-00000"}, headers=headers).status_code == 422
        assert client.post("/api/customer/password", json={**body, "new_password": PASSWORD, "confirm_password": PASSWORD}, headers=headers).status_code == 422
        assert client.post("/api/customer/password", json={**body, "new_password": "weak", "confirm_password": "weak"}, headers=headers).status_code == 422
        done = client.post("/api/customer/password", json=body, headers=headers)
        assert done.status_code == 200 and done.json()["email"] == "logged" and done.json()["sms"] == "logged"
        # The old password stops working and the new one works.
        assert client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": PASSWORD, "portal": "customer"}).status_code == 401
        assert client.post("/api/auth/login", json={"email": username_for("cus_nimal"), "password": "Lagoon-86420", "portal": "customer"}).status_code == 200
    stored = _user()
    assert verify_password("Lagoon-86420", stored.password_hash) and stored.password_changed_at is not None
    with session_scope() as session:
        notes = session.query(Notification).filter(Notification.user_id == "cus_nimal", Notification.title.in_(["Your PayShield.lk password was changed", "SMS"])).all()
        texts = {n.channel: n.body for n in notes}
    assert "was changed" in texts["email"] and "was changed" in texts["sms"]
    assert "http" not in texts["sms"].lower()


def test_wrong_old_password_is_throttled(boot):
    with TestClient(app) as client:
        register(client)
        headers = sign_in(client)
        body = {"old_password": "Guess-0001", "new_password": "Lagoon-86420", "confirm_password": "Lagoon-86420"}
        for _ in range(6):
            assert client.post("/api/customer/password", json=body, headers=headers).status_code == 401
        assert client.post("/api/customer/password", json=body, headers=headers).status_code == 429


def test_abnormal_case_forces_a_password_change_and_the_workflow_is_unchanged(boot):
    with TestClient(app) as client:
        register(client)
        headers = sign_in(client)
        assert client.get("/api/customer/home", headers=headers).json()["customer"]["must_change_password"] is False
        case_id = trigger("cus_nimal", "sim_swap_transfer")
        begin_case(case_id)
        with session_scope() as session:
            assert session.get(Case, case_id).status == "waiting_customer"
        # Not me: blocked as before, and the password change becomes required.
        client.post(f"/api/customer/cases/{case_id}/respond", json={"decision": "no"}, headers=headers)
        with session_scope() as session:
            assert session.get(Case, case_id).status == "closed_blocked"
        assert client.get("/api/customer/home", headers=headers).json()["customer"]["must_change_password"] is True
        client.post(
            "/api/customer/password",
            json={"old_password": PASSWORD, "new_password": "Lagoon-86420", "confirm_password": "Lagoon-86420"},
            headers=headers,
        )
        assert client.get("/api/customer/home", headers=headers).json()["customer"]["must_change_password"] is False


def test_yes_path_still_releases_and_the_balance_is_debited_once(boot):
    start = record("cus_nimal")["balance"]
    with TestClient(app) as client:
        register(client)
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer")
    assert resume_case(case_id, live_selfie(), "waiting_liveness")
    assert resume_case(case_id, {"answers": correct_answers(case_id)}, "waiting_verification")
    with session_scope() as session:
        assert session.get(Case, case_id).status == "closed_released"
    assert _user().balance_lkr == start - 200_000
    assert _user().must_change_password is True


def test_blocked_payment_leaves_the_balance_alone(boot):
    start = record("cus_dilani")["balance"]
    with TestClient(app) as client:
        register(client, "cus_dilani")
    case_id = trigger("cus_dilani", "port_out_critical")
    begin_case(case_id)
    row = _user("cus_dilani")
    assert row.balance_lkr == start
    assert row.must_change_password is True


def test_routine_release_debits_the_balance_and_does_not_force_a_new_password(boot):
    start = record("cus_ravi")["balance"]
    with TestClient(app) as client:
        register(client, "cus_ravi")
    case_id = trigger("cus_ravi", "routine_payment")
    begin_case(case_id)
    row = _user("cus_ravi")
    assert row.balance_lkr == start - 4850
    assert row.must_change_password is False


def test_console_adds_a_bank_record_that_the_customer_then_claims(boot):
    from app.runtime import runtime
    from app.security import create_token

    token = create_token(runtime.settings, "usr_analyst", "fraud_analyst")
    with TestClient(app) as client:
        client.get("/api/health")
        questions = client.get("/api/questions", headers={"Authorization": "Bearer " + token}).json()["questions"]
        body = {
            "full_name": "Priya Fernando",
            "phone": "0771234567",
            "email": "priya.f@mail.lk",
            "nic": "199512345678",
            "account_number": "900112233",
            "district": "Galle",
            "branch": "Galle Fort",
            "balance_lkr": 85_000,
            "answers": [{"code": q["code"], "answer": "x" + q["code"]} for q in questions[:6]],
        }
        made = client.post("/api/enrol", json=body, headers={"Authorization": "Bearer " + token})
        assert made.status_code == 200, made.text
        # Not registered yet, so the password it was created with cannot sign anyone in.
        assert client.post("/api/auth/login", json={"email": "Priya Fernando", "password": "anything-123", "portal": "customer"}).status_code == 401
        joined = client.post(
            "/api/customer/register",
            json={
                "full_name": "Priya Fernando",
                "email": "priya.f@mail.lk",
                "nic": "199512345678",
                "phone": "+94771234567",
                "account_number": "900112233",
                "password": "Orchard-5791",
                "confirm_password": "Orchard-5791",
                "frames": frames(),
            },
        )
        assert joined.status_code == 200, joined.text
        info = client.get("/api/customer/account", headers={"Authorization": "Bearer " + authenticate(client, joined.json())["token"]}).json()
    assert info["bank"] == "BankCylone" and info["balance_lkr"] == 85_000 and info["branch"] == "Galle Fort"
