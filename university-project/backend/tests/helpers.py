import base64
import os

from app.seed import CUSTOMERS

PASSWORD = "Harbour-2468"


def record(customer_id: str) -> dict:
    return next(row for row in CUSTOMERS if row["id"] == customer_id)


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
    }
    body.update(overrides)
    return body


def register(client, customer_id: str = "cus_nimal", **overrides):
    return client.post("/api/customer/register", json=registration(customer_id, **overrides))


def sign_in(client, customer_id: str = "cus_nimal", password: str = PASSWORD) -> dict:
    row = record(customer_id)
    reply = client.post("/api/auth/login", json={"email": row["name"], "password": password, "portal": "customer"})
    assert reply.status_code == 200, reply.text
    return {"Authorization": "Bearer " + reply.json()["token"]}


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
