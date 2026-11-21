"""Contract tests: what a caller gets back, and that malformed input of every kind is answered with a plain 4xx.

The API's routes return plain dictionaries, so the OpenAPI schema says nothing about response shapes. The shape
tests below pin the top-level fields the web console and the customer site read, so a rename shows up here
before it shows up as a blank screen."""
import pytest
from test_api_matrix import (  # noqa: F401
    ALLOWED_PAIRS,
    OPS,
    POLICY,
    PROTECTED,
    _booted,
    _call,
    _headers,
    body_for,
    client,
    example,
    url_for,
)

from app.main import app

BAD_VALUES = [None, 12345, -1, 3.5, True, [], {}, [1, "a", None], {"a": {"b": [1]}}, "x" * 20_000, "", " ", "\u0000", "‮﻿", "1e999"]
BAD_IDS = ["../../etc/passwd", "..%2f..%2fsecret", "%00", "a" * 3000, "💥", "' OR 1=1 --", "<script>alert(1)</script>", "${jndi:ldap://x}", "{{7*7}}", "null", "-1", " "]
LEAKS = ("Traceback", 'File "/', ".py\", line", "sqlalchemy", "sqlite3.", "psycopg")


def first_role(op):
    return sorted(POLICY[op])[0] if POLICY[op] != "public" else None


def json_ops():
    return [op for op in OPS if op[0] == "POST" and (app.openapi()["paths"][op[1]]["post"].get("requestBody"))]


def assert_clean(reply, what):
    assert reply.status_code < 500, (what, reply.status_code, reply.text[:200])
    for leak in LEAKS:
        assert leak not in reply.text, (what, "the error shows internals: " + leak)
    if reply.status_code >= 400 and "json" in reply.headers.get("content-type", ""):
        body = reply.json()
        camara = {"status", "code", "message"} <= set(body) if isinstance(body, dict) else False  # the operator-style API answers in CAMARA's own error shape
        assert "detail" in body or camara, (what, reply.text[:200])


@pytest.mark.parametrize("op", json_ops(), ids=lambda o: f"{o[0]} {o[1]}")
def test_wrong_types_in_every_field_get_a_plain_4xx(client, op):
    role = first_role(op)
    valid = body_for(op)
    fields = list(valid) if isinstance(valid, dict) else []
    for name in fields or ["_"]:
        for bad in BAD_VALUES:
            body = {**valid, name: bad} if fields else bad
            reply = client.request("POST", url_for(op[1]), headers=_headers(role), json=body)
            assert_clean(reply, f"{op} field {name}={str(bad)[:20]!r}")


@pytest.mark.parametrize("op", json_ops(), ids=lambda o: f"{o[0]} {o[1]}")
def test_a_missing_or_unreadable_body_is_a_4xx(client, op):
    role = first_role(op)
    headers = _headers(role)
    for payload, ctype in [(b"", "application/json"), (b"{not json", "application/json"), (b"[1,2", "application/json"), (b"null", "application/json"),
                           (b"\xff\xfe\x00", "application/json"), (b"a=b&c=d", "application/x-www-form-urlencoded"), (b"plain words", "text/plain"),
                           (b"{" * 5000, "application/json"), (b"[" * 5000, "application/json")]:
        reply = client.request("POST", url_for(op[1]), headers={**headers, "content-type": ctype}, content=payload)
        assert_clean(reply, f"{op} {payload[:12]!r}")


@pytest.mark.parametrize("op", json_ops(), ids=lambda o: f"{o[0]} {o[1]}")
def test_a_very_large_body_is_refused_or_ignored_not_crashed(client, op):
    role = first_role(op)
    huge = '{"a":"' + "x" * 3_000_000 + '"}'
    reply = client.request("POST", url_for(op[1]), headers={**_headers(role), "content-type": "application/json"}, content=huge)
    assert_clean(reply, f"{op} 3 MB")


ID_OPS = [op for op in OPS if "{" in op[1] and POLICY.get(op) not in (None, "public")]


@pytest.mark.parametrize("op", ID_OPS, ids=lambda o: f"{o[0]} {o[1]}")
def test_odd_identifiers_in_the_path_are_a_clean_not_found(client, op):
    role = first_role(op)
    key = op[1][op[1].index("{") + 1: op[1].index("}")]
    for bad in BAD_IDS:
        path = op[1].replace("{" + key + "}", bad.replace("/", "%2F").replace("#", "%23").replace("?", "%3F"))
        for other in ("request_id", "user_id", "case_id", "proposal_id", "report_id", "transaction_id"):
            path = path.replace("{" + other + "}", "none")
        kwargs = {"json": body_for(op)} if op[0] == "POST" else {}
        reply = client.request(op[0], path, headers=_headers(role), **kwargs)
        assert_clean(reply, f"{op} id={bad[:20]!r}")
        assert reply.status_code != 200, (op, bad)


@pytest.mark.parametrize("op", [o for o in PROTECTED if o[0] == "GET"], ids=lambda o: f"{o[0]} {o[1]}")
def test_a_successful_read_is_json_or_a_declared_file(client, op):
    role = first_role(op)
    reply = client.get(url_for(op[1]), headers=_headers(role))
    if reply.status_code != 200:
        return assert_clean(reply, str(op))
    ctype = reply.headers["content-type"]
    assert ctype.startswith(("application/json", "text/markdown", "application/pdf", "text/plain")), ctype
    if ctype.startswith("application/json"):
        assert isinstance(reply.json(), (dict, list))


def test_unknown_paths_and_methods_are_clean(client):
    for method, path in [("GET", "/api/nothing"), ("POST", "/api/overview"), ("DELETE", "/api/cases"), ("PUT", "/api/me"), ("GET", "/api/cases/"), ("GET", "//api//me"),
                         ("OPTIONS", "/api/me"), ("GET", "/api/%00"), ("GET", "/" + "a" * 5000)]:
        reply = client.request(method, path)
        assert_clean(reply, f"{method} {path[:30]}")
        assert reply.status_code in {200, 307, 308, 400, 401, 403, 404, 405}


# ------------------------------------------------------------------ the fields the screens read

SHAPES = {
    ("GET", "/api/me"): {"id", "email", "role"},
    ("GET", "/api/overview"): None,
    ("GET", "/api/cases"): None,
    ("GET", "/api/holds"): None,
    ("GET", "/api/analytics"): {"totals", "by_province", "by_district", "daily", "findings", "summary", "trend", "hotspots", "rising"},
    ("GET", "/api/agents"): None,
    ("GET", "/api/charters"): None,
    ("GET", "/api/calibration"): None,
    ("GET", "/api/security/status"): None,
    ("GET", "/api/simulate"): {"scenarios"},
    ("GET", "/api/emergency"): None,
    ("GET", "/api/demo/customers"): {"customers", "scenarios"},
    ("GET", "/api/customer/home"): None,
    ("GET", "/api/customer/account"): None,
}


@pytest.mark.parametrize("op", sorted(SHAPES), ids=lambda o: o[1])
def test_the_fields_a_screen_depends_on_are_present(client, op):
    role = first_role(op) if op in POLICY else "customer"
    role = "super_admin" if role == "auditor" else role
    reply = client.get(url_for(op[1]), headers=_headers(role))
    assert reply.status_code == 200, reply.text[:200]
    data = reply.json()
    wanted = SHAPES[op]
    if wanted:
        assert wanted <= set(data), wanted - set(data)
    assert data not in ({}, []) or op[1] in {"/api/holds", "/api/cases"}


def test_the_case_detail_shape_is_what_the_case_page_reads(client):
    from app.agents.runner import begin_case
    from app.demo import trigger

    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    data = client.get(f"/api/cases/{case_id}", headers=_headers("fraud_analyst")).json()
    assert {"case", "customer", "transaction", "events", "report"} <= set(data)
    assert {"id", "status", "risk_score", "policy_decision", "risk_factors", "telecom_summary", "banking_summary"} <= set(data["case"])
    assert {"id", "amount_lkr", "payee", "status"} <= set(data["transaction"])
    assert data["events"] and {"agent", "state", "created_at", "message"} <= set(data["events"][0])
    # Operators never see the customer's stored face or security answers.
    blob = str(data)
    for secret in ("face_ref", "face_hash", "password_hash", "gcm$", "pbkdf2$", "totp_secret"):
        assert secret not in blob


def test_the_customer_views_never_carry_secrets_or_scores(client):
    for path in ("/api/customer/home", "/api/customer/account"):
        blob = client.get(path, headers=_headers("customer")).text
        for secret in ("face_ref", "face_hash", "password_hash", "gcm$", "pbkdf2$", "totp_secret", "risk_score", "risk_factors", "policy_decision"):
            assert secret not in blob, (path, secret)


def test_error_bodies_use_one_shape(client):
    samples = [client.get("/api/overview"), client.get("/api/cases/none", headers=_headers("fraud_analyst")), client.post("/api/auth/login", json={}),
               client.post("/api/auth/login", json={"email": "a@b.lk", "password": "wrong-password-1"}), client.get("/api/admin/audit", headers=_headers("fraud_analyst"))]
    for reply in samples:
        assert reply.status_code >= 400 and "detail" in reply.json()


def test_a_value_the_database_refuses_is_a_422_not_a_500(client):
    """Postgres refuses over-long text and NUL bytes that SQLite accepts. The handler turns that into bad input."""
    from sqlalchemy.exc import DataError

    def refuses():
        raise DataError("INSERT ...", {}, Exception("value too long for type character varying(255)"))

    route_count = len(app.router.routes)
    app.add_api_route("/__test_data_error", refuses, methods=["GET"])
    try:
        reply = client.get("/__test_data_error")
    finally:
        del app.router.routes[route_count:]
    assert reply.status_code == 422 and "not valid" in reply.json()["detail"]
    assert "varying" not in reply.text and "INSERT" not in reply.text


def test_a_customer_added_through_the_console_has_a_position_and_the_map_never_gets_a_null(client):
    from app.db import session_scope
    from app.models import User

    admin = _headers("super_admin")
    listed = client.get("/api/questions", headers=_headers("fraud_analyst")).json()
    questions = [q["code"] for q in (listed["questions"] if isinstance(listed, dict) else listed)][:6]
    assert len(questions) == 6
    body = {"full_name": "Kasun Ratnayake", "email": "kasun.map@mail.lk", "nic": "199012345678", "phone": "+94771230098", "account_number": "8200459988",
            "district": "Jaffna", "branch": "Main", "balance_lkr": 1000, "answers": [{"code": c, "answer": "answer " + c} for c in questions]}
    made = client.post("/api/enrol", json=body, headers=admin)
    assert made.status_code == 200, made.text
    with session_scope() as session:
        row = session.get(User, made.json()["user_id"])
        assert (row.lat, row.lng) == (9.6615, 80.0255)
        row.lat = row.lng = None  # an older record that never had a position
    points = client.get("/api/map", headers=admin).json()["customers"]
    assert points and all(p["lat"] is not None and p["lng"] is not None for p in points)
    assert made.json()["user_id"] not in {p["id"] for p in points}


def test_every_district_has_a_position_inside_sri_lanka():
    from app.geo import COORDS, DISTRICTS, coordinates_of

    assert set(COORDS) == set(DISTRICTS)
    for name, (lat, lng) in COORDS.items():
        assert 5.8 <= lat <= 9.9 and 79.4 <= lng <= 82.0, name
    assert coordinates_of("Atlantis") is None and coordinates_of(None) is None
