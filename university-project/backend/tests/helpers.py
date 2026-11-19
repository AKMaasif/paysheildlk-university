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
