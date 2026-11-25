from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import analytics
from app.analytics import compute
from app.charters import CharterViolation, enforce
from app.db import session_scope
from app.facts import recent_transactions
from app.geo import DISTRICTS, PROVINCES
from app.main import app
from app.models import Case, Transaction
from app.runtime import runtime
from app.security import create_token
from app.simbook import BOOK_CUSTOMER, BOOK_VERSION, refresh
from app.util import utcnow

COLOMBO = ZoneInfo("Asia/Colombo")


def _noon(age_days: int) -> datetime:
    today = utcnow().astimezone(COLOMBO).date()
    return datetime.combine(today - timedelta(days=age_days), datetime.min.time(), tzinfo=COLOMBO).replace(hour=12)


_counter = [0]


def _pay(session, district, age, outcome="normal", amount=10_000, customer=BOOK_CUSTOMER, **extra):
    """One simulated-style payment. outcome: normal, blocked, held or released."""
    _counter[0] += 1
    origin = extra.pop("origin", "simulated")
    session.add(
        Transaction(
            id=f"T-{_counter[0]:05d}",
            customer_id=customer,
            kind="transfer",
            amount_lkr=amount,
            payee="x",
            status={"normal": "completed"}.get(outcome, outcome),
            occurred_at=_noon(age),
            origin=origin,
            # A real payment carries no district of its own: pass district=None.
            detail=({"district": district} if district else {}) | ({"flagged": outcome != "normal", "v": BOOK_VERSION} if origin == "simulated" else {}),
            **extra,
        )
    )


def _empty_ledger():
    with session_scope() as session:
        session.query(Transaction).delete()


def _auth(user_id, role):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


def test_the_simulated_book_has_structure_and_stays_out_of_customer_ledgers(boot):
    with session_scope() as session:
        rows = session.query(Transaction).filter(Transaction.origin == "simulated").all()
        per_district = {}
        for row in rows:
            per_district[row.detail["district"]] = per_district.get(row.detail["district"], 0) + 1
        assert set(per_district) == set(DISTRICTS)
        # Volume follows population, so Colombo is far busier than Mullaitivu. The old seed gave every district 60.
        assert per_district["Colombo"] > 3 * per_district["Mullaitivu"]
        assert {row.customer_id for row in rows} == {BOOK_CUSTOMER}
    assert all(item["payee"] != "Simulated payee" for item in recent_transactions("cus_nimal", limit=50))


def test_totals_districts_and_provinces_add_up(boot):
    _empty_ledger()
    with session_scope() as session:
        for _ in range(10):
            _pay(session, "Kandy", 3)
        _pay(session, "Kandy", 2, "blocked", 200_000)
        _pay(session, "Kandy", 2, "blocked", 300_000)
        _pay(session, "Kandy", 1, "held", 150_000)
        _pay(session, "Kandy", 1, "released", 90_000)
        for _ in range(20):
            _pay(session, "Colombo", 4)
        # A login is not a payment.
        session.add(Transaction(id="T-login", customer_id="cus_nimal", kind="login", amount_lkr=0, status="completed", occurred_at=_noon(1), origin="demo", detail={}))
    out = compute()
    assert out["totals"]["payments"] == 34
    assert (out["totals"]["flagged"], out["totals"]["blocked"], out["totals"]["held"], out["totals"]["released"]) == (4, 2, 1, 1)
    assert out["totals"]["amount_stopped_lkr"] == 500_000
    assert out["totals"]["amount_held_lkr"] == 150_000
    assert out["totals"]["flag_rate"] == round(100 * 4 / 34, 1)
    kandy = next(row for row in out["by_district"] if row["district"] == "Kandy")
    assert (kandy["payments"], kandy["flagged"], kandy["province"]) == (14, 4, "Central")
    assert len(out["by_district"]) == 25 and len(out["by_province"]) == len(PROVINCES) == 9
    central = next(row for row in out["by_province"] if row["province"] == "Central")
    assert central["flagged"] == 4 and central["share"] == 100.0
    assert sum(row["payments"] for row in out["by_district"]) == out["totals"]["payments"]
    assert len(out["daily"]) == 30 and out["daily"][-1]["partial"] is True
    assert sum(day["payments"] for day in out["daily"]) == 34


def test_real_payments_take_their_district_and_flag_from_the_case(boot):
    _empty_ledger()
    now = utcnow()
    with session_scope() as session:
        # Nimal lives in Colombo. Neither payment carries a district of its own.
        for case_id, decision in (("C-held", "hold"), ("C-clear", "release")):
            session.add(Case(id=case_id, customer_id="cus_nimal", status="closed_released", policy_decision=decision, created_at=now, updated_at=now))
        _pay(session, None, 1, "released", customer="cus_nimal", origin="demo", case_id="C-held")
        _pay(session, None, 1, "released", customer="cus_nimal", origin="demo", case_id="C-clear")
    out = compute()
    colombo = next(row for row in out["by_district"] if row["district"] == "Colombo")
    # Both are "released", but only the one the rules held counts as abnormal.
    assert (colombo["payments"], colombo["flagged"], colombo["released"]) == (2, 1, 1)
    assert out["label"] == "recorded"


def test_a_hot_spot_and_a_sudden_riser_are_found(boot):
    _empty_ledger()
    with session_scope() as session:
        for _ in range(300):
            _pay(session, "Colombo", 15)
        for age in range(8, 26):
            for _ in range(6):
                _pay(session, "Kandy", age)
        _pay(session, "Kandy", 12, "blocked")
        # This week Kandy jumps.
        for age in range(0, 7):
            for _ in range(2):
                _pay(session, "Kandy", age)
        for age in range(0, 7):
            _pay(session, "Kandy", age, "blocked")
        _pay(session, "Kandy", 0, "held")
    out = compute()
    assert [spot["district"] for spot in out["hotspots"]] == ["Kandy"]
    assert out["hotspots"][0]["multiple"] > 1.5
    riser = out["rising"][0]
    assert riser["district"] == "Kandy" and riser["last7_flagged"] == 8 and riser["expected"] < 1
    assert any("Kandy" in line and "7 days" in line for line in out["findings"])
    assert "Kandy" in out["summary"]


def test_a_quiet_book_reports_no_hot_spots(boot):
    _empty_ledger()
    with session_scope() as session:
        for name in DISTRICTS:
            for _ in range(45):
                _pay(session, name, 5)
    out = compute()
    assert out["hotspots"] == [] and out["rising"] == []
    assert out["totals"]["flagged"] == 0 and out["trend"]["change_pct"] is None


def test_a_reworded_brief_with_an_invented_number_is_dropped(boot, monkeypatch):
    analytics._brief_cache.clear()
    monkeypatch.setattr(runtime.settings, "meta_api_key", "test-key")

    class Reply:
        provider = "meta"

        def __init__(self, text):
            self.text = text

    replies = iter([Reply("About 9,999 payments looked abnormal in the last 30 days."), Reply("Payments were checked.")])
    monkeypatch.setattr(runtime.llm, "complete", lambda **_kw: next(replies))
    facts = "120 payments in the last 30 days; 7 looked abnormal."
    text, source = analytics._brief(facts, ["fallback text"])
    assert (text, source) == ("fallback text", "rules")
    analytics._brief_cache.clear()
    text, source = analytics._brief(facts + " ", ["fallback text"])
    assert (text, source) == ("Payments were checked.", "meta")


def test_the_brief_comes_from_rules_when_no_model_is_configured(boot, monkeypatch):
    analytics._brief_cache.clear()
    called = []
    monkeypatch.setattr(runtime.llm, "complete", lambda **_kw: called.append(1))
    out = compute()
    assert out["summary_source"] == "rules" and not called
    assert out["summary"] == out["narrative"]


def test_endpoint_is_for_operators_only(boot):
    with TestClient(app) as client:
        customer = client.get("/api/analytics", headers=_auth("cus_nimal", "customer"))
        assert customer.status_code == 403
        anonymous = client.get("/api/analytics")
        assert anonymous.status_code in {401, 403}
        auditor = client.get("/api/analytics", headers=_auth("usr_auditor", "auditor"))
        analyst = client.get("/api/analytics", headers=_auth("usr_analyst", "fraud_analyst"))
    assert analyst.status_code == 200
    body = analyst.json()
    assert len(body["by_district"]) == 25 and len(body["by_province"]) == 9
    # No customer identifiers leave the analytics agent.
    text = analyst.text
    for private in ("Nimal", "nimal.perera", "8200451190", "+94772451183"):
        assert private not in text
    # Read-only roles may see aggregates too.
    assert auditor.status_code == 200


def test_the_analytics_agent_cannot_move_money_or_contact_anyone():
    enforce("analytics", "aggregate_payments")
    enforce("analytics", "write_brief")
    for tool in ("block_transaction", "release_transaction", "lock_account", "send_sms", "generate_report"):
        with pytest.raises(CharterViolation):
            enforce("analytics", tool)


def test_an_old_simulated_book_is_rebuilt_once(boot):
    with session_scope() as session:
        session.query(Transaction).filter(Transaction.origin == "simulated").delete()
        session.add(Transaction(id="SIMU-old", customer_id="cus_nimal", kind="transfer", amount_lkr=1, status="blocked", occurred_at=utcnow(), origin="simulated", detail={"district": "Colombo"}))
    with session_scope() as session:
        assert refresh(session, utcnow()) is True
    with session_scope() as session:
        assert session.get(Transaction, "SIMU-old") is None
        assert session.query(Transaction).filter(Transaction.origin == "simulated").count() > 2000
        assert refresh(session, utcnow()) is False
