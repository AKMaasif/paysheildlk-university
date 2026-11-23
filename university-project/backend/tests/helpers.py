import base64
import os

from app.seed import CUSTOMERS

PASSWORD = "Harbour-2468"


def record(customer_id: str) -> dict:
    return next(row for row in CUSTOMERS if row["id"] == customer_id)


def username_for(customer_id: str = "cus_nimal") -> str:
    """The username the tests choose at registration: the name with dots, for example nimal.perera."""
    return record(customer_id)["name"].lower().replace(" ", ".")


def frames() -> list[str]:
    """Two different camera-like frames, large and busy enough to pass the live check."""
    return [base64.b64encode(os.urandom(1500)).decode(), base64.b64encode(os.urandom(1500)).decode()]


def registration(customer_id: str = "cus_nimal", **overrides) -> dict:
    row = record(customer_id)
    body = {
        "full_name": row["name"],
        "email": row["email"],
        "nic": row["nic"],
        "phone": row["phone"],
        "account_number": row["account"],
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "frames": frames(),
        "username": username_for(customer_id),
    }
    body.update(overrides)
    return body


def register(client, customer_id: str = "cus_nimal", **overrides):
    return client.post("/api/customer/register", json=registration(customer_id, **overrides))


def authenticate(client, pending: dict) -> dict:
    """Finish a customer sign-in that stopped at the authenticator step, the way a customer does: read the code from the
    app (here, computed from the stored secret) and send it. A first-time customer sets the authenticator up first."""
    from app import security
    from app.db import session_scope
    from app.models import User

    token = pending["pending_token"]
    headers = {"Authorization": "Bearer " + token}
    if pending["step"] == "enrol":
        setup = client.post("/api/customer/totp/setup", headers=headers)
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]
        security._used_codes.clear()
        done = client.post("/api/customer/totp/confirm", headers=headers, json={"code": security.totp_now(secret)})
        assert done.status_code == 200, done.text
        return done.json()
    pid = security.decode_token(__import__("app.runtime", fromlist=["runtime"]).runtime.settings, token, purpose="totp")["sub"]
    with session_scope() as session:
        secret = security.open_totp(session.get(User, pid).totp_secret)
    security._used_codes.clear()
    done = client.post("/api/auth/totp", json={"pending_token": token, "code": security.totp_now(secret)})
    assert done.status_code == 200, done.text
    return done.json()


def sign_in(client, customer_id: str = "cus_nimal", password: str = PASSWORD) -> dict:
    reply = client.post("/api/auth/login", json={"email": username_for(customer_id), "password": password, "portal": "customer"})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert "token" not in body, "a customer must not get a session from the password alone"
    return {"Authorization": "Bearer " + authenticate(client, body)["token"]}


def live_selfie() -> dict:
    """A selfie step the simple check accepts: two different, detailed frames. There is no way to claim a verdict."""
    return {"frames": [os.urandom(1500), os.urandom(1500)]}


def fake_selfie() -> dict:
    """A still photo: the same frame twice. The simple check refuses it."""
    still = os.urandom(1500)
    return {"frames": [still, still]}


def correct_answers(case_id: str, customer_id: str = "cus_nimal") -> dict:
    """The right answers to the questions this case is currently asking. A round draws three of the customer's eight."""
    from app.db import session_scope
    from app.models import Case
    from app.seed import PILOT_ANSWERS

    with session_scope() as session:
        asked = [item["id"] for item in session.get(Case, case_id).pending_prompt["questions"]]
    return {code: PILOT_ANSWERS[customer_id][code] for code in asked}


def external_services() -> tuple[str | None, str | None]:
    """A real Postgres and Redis when TEST_DATABASE_URL and TEST_REDIS_URL are set, emptied first. Otherwise (None, None)."""
    database, redis_url = os.environ.get("TEST_DATABASE_URL"), os.environ.get("TEST_REDIS_URL")
    if database:
        import psycopg

        from app.runtime import psycopg_url

        with psycopg.connect(psycopg_url(database), autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("CREATE SCHEMA public")
    if redis_url:
        import redis

        redis.Redis.from_url(redis_url).flushall()
    return database, redis_url
