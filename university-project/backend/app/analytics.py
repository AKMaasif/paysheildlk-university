"""Fraud analytics for the main dashboard: the Analytics agent's work.

The agent reads payments in aggregate and writes a short brief. It never sees a
customer name, phone number or account, and it cannot move money (see its
charter). Every figure on the dashboard is computed here from the ledger. A
language model may reword the summary when one is configured, but a reworded
summary is thrown away if it contains any number that is not in the facts.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.charters import enforce
from app.db import session_scope
from app.geo import DISTRICTS, PROVINCES, province_of
from app.models import Case, Transaction, User
from app.util import aware, iso, lkr, utcnow

COLOMBO = ZoneInfo("Asia/Colombo")
WINDOW_DAYS = 30
NOT_PAYMENTS = {"login", "new_payee", "password_reset"}
MIN_PAYMENTS_FOR_HOTSPOT = 40
MIN_FLAGGED_FOR_HOTSPOT = 6

_brief_cache: dict[str, tuple[str, str]] = {}

BRIEF_SYSTEM = (
    "You write a four-sentence fraud brief for a bank's fraud desk from the facts you are given. "
    "Use only numbers that appear in the facts. Do not add causes, names or advice. Plain sentences, no lists."
)


def _local_day(moment: datetime) -> date:
    return aware(moment).astimezone(COLOMBO).date()


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _gather(since_day: date) -> list[dict]:
    """One flat record per payment in the window. Customer details stay behind."""
    with session_scope() as session:
        txs = (
            session.query(Transaction)
            .filter(Transaction.occurred_at >= datetime.combine(since_day, datetime.min.time(), tzinfo=COLOMBO))
            .all()
        )
        district_of_user = {row.id: row.district for row in session.query(User).filter(User.role == "customer")}
        policy_of_case = {row.id: row.policy_decision for row in session.query(Case.id, Case.policy_decision)}
        out: list[dict] = []
        for tx in txs:
            if tx.kind in NOT_PAYMENTS:
                continue
            detail = tx.detail or {}
            simulated = tx.origin == "simulated"
            district = detail.get("district") or district_of_user.get(tx.customer_id) or "Unknown"
            if simulated:
                flagged = bool(detail.get("flagged"))
            elif tx.case_id:
                flagged = policy_of_case.get(tx.case_id) in {"hold", "block"}
            else:
                flagged = tx.status in {"held", "blocked"}
            if tx.status == "blocked":
                outcome = "blocked"
            elif tx.status == "held" or (flagged and tx.status == "pending"):
                outcome = "held"
            elif flagged and tx.status == "released":
                outcome = "released"
            else:
                outcome = "normal"
            out.append(
                {
                    "day": _local_day(tx.occurred_at),
                    "district": district,
                    "flagged": outcome != "normal",
                    "outcome": outcome,
                    "amount": tx.amount_lkr or 0,
                    "simulated": simulated,
                }
            )
        return out


def _bucket() -> dict:
    return {"payments": 0, "flagged": 0, "blocked": 0, "held": 0, "released": 0}


def _add(bucket: dict, record: dict) -> None:
    bucket["payments"] += 1
    if record["flagged"]:
        bucket["flagged"] += 1
        bucket[record["outcome"]] += 1


def _with_rate(name_key: str, name: str, bucket: dict, extra: dict | None = None) -> dict:
    row = {name_key: name, **bucket, "rate": _pct(bucket["flagged"], bucket["payments"]), "count": bucket["payments"]}
    if extra:
        row.update(extra)
    return row


def compute(now: datetime | None = None) -> dict:
    enforce("analytics", "aggregate_payments")
    moment = aware(now or utcnow())
    today = moment.astimezone(COLOMBO).date()
    first = today - timedelta(days=WINDOW_DAYS - 1)
    records = [r for r in _gather(first) if first <= r["day"] <= today]

    totals = _bucket()
    stopped = held_amount = 0
    daily: dict[date, dict] = {first + timedelta(days=i): _bucket() for i in range(WINDOW_DAYS)}
    by_district: dict[str, dict] = {name: _bucket() for name in DISTRICTS}
    by_province: dict[str, dict] = {name: _bucket() for name in PROVINCES}
    recent: dict[str, dict] = defaultdict(_bucket)  # last 7 days
    earlier: dict[str, dict] = defaultdict(_bucket)  # the 23 days before that
    simulated = 0
    for record in records:
        _add(totals, record)
        _add(daily[record["day"]], record)
        if record["district"] in by_district:
            _add(by_district[record["district"]], record)
            _add(by_province[province_of(record["district"])], record)
        _add((recent if record["day"] > today - timedelta(days=7) else earlier)[record["district"]], record)
        if record["outcome"] == "blocked":
            stopped += record["amount"]
        elif record["outcome"] == "held":
            held_amount += record["amount"]
        simulated += record["simulated"]

    nat_rate = _pct(totals["flagged"], totals["payments"])

    districts = [
        _with_rate("district", name, bucket, {"province": province_of(name)}) for name, bucket in by_district.items()
    ]
    provinces = [
        _with_rate("province", name, bucket, {"share": _pct(bucket["flagged"], totals["flagged"])})
        for name, bucket in by_province.items()
    ]
    districts.sort(key=lambda row: (-row["flagged"], row["district"]))
    provinces.sort(key=lambda row: (-row["flagged"], row["province"]))

    # Persistent hot spots: a flag rate well above the national one, over enough payments to mean something.
    hotspots = []
    for row in districts:
        if (
            row["payments"] >= MIN_PAYMENTS_FOR_HOTSPOT
            and row["flagged"] >= MIN_FLAGGED_FOR_HOTSPOT
            and nat_rate > 0
            and row["rate"] >= 1.5 * nat_rate
        ):
            hotspots.append({"district": row["district"], "rate": row["rate"], "multiple": round(row["rate"] / nat_rate, 1), "flagged": row["flagged"]})
    hotspots.sort(key=lambda row: -row["multiple"])

    # Sudden risers: this week against the district's own usual rate over the weeks before.
    rising = []
    for name in DISTRICTS:
        now_b, before_b = recent[name], earlier[name]
        if now_b["flagged"] < 4 or now_b["payments"] == 0:
            continue
        usual = before_b["flagged"] / before_b["payments"] if before_b["payments"] else 0.0
        expected = usual * now_b["payments"]
        if now_b["flagged"] >= max(4.0, 2.5 * expected):
            rising.append(
                {
                    "district": name,
                    "last7_flagged": now_b["flagged"],
                    "last7_payments": now_b["payments"],
                    "expected": round(expected, 1),
                    "last7_rate": _pct(now_b["flagged"], now_b["payments"]),
                    "usual_rate": round(100 * usual, 1),
                }
            )
    rising.sort(key=lambda row: -(row["last7_flagged"] - row["expected"]))

    days = sorted(daily)
    # Today is a partial day, so the week-on-week comparison uses full days only.
    last7_flagged = sum(daily[d]["flagged"] for d in days[-8:-1])
    prev7_flagged = sum(daily[d]["flagged"] for d in days[-15:-8])
    change = round(100 * (last7_flagged - prev7_flagged) / prev7_flagged) if prev7_flagged else None
    trend = {"last7_flagged": last7_flagged, "prev7_flagged": prev7_flagged, "change_pct": change}

    facts, findings = _findings(totals, nat_rate, stopped, held_amount, provinces, districts, hotspots, rising, trend)
    summary, source = _brief(facts, findings)

    return {
        "label": "simulated" if simulated else "recorded",
        "window_days": WINDOW_DAYS,
        "generated_at": iso(moment),
        "sources": {"simulated": simulated, "recorded": totals["payments"] - simulated},
        "totals": {**totals, "flag_rate": nat_rate, "amount_stopped_lkr": stopped, "amount_held_lkr": held_amount},
        "trend": trend,
        "daily": [{"date": d.isoformat(), "partial": d == today, **daily[d]} for d in days],
        "by_province": provinces,
        "by_district": districts,
        "hotspots": hotspots[:5],
        "rising": rising[:5],
        "findings": findings,
        "summary": summary,
        "summary_source": source,
        # Fields the first dashboard version read.
        "total": totals["payments"],
        "blocked": totals["blocked"],
        "held": totals["held"],
        "released": totals["released"],
        "fraud_rate": _pct(totals["blocked"], totals["payments"]),
        "narrative": summary,
    }


def _findings(totals, nat_rate, stopped, held_amount, provinces, districts, hotspots, rising, trend) -> tuple[str, list[str]]:
    lines = [
        f"{totals['payments']:,} payments in the last {WINDOW_DAYS} days; {totals['flagged']:,} ({nat_rate}%) looked abnormal.",
        f"Of those, {totals['blocked']:,} were blocked, {totals['held']:,} are still held and {totals['released']:,} were confirmed by the customer and released.",
        f"{lkr(stopped)} was stopped and {lkr(held_amount)} is held.",
    ]
    if provinces and provinces[0]["flagged"]:
        top = provinces[0]
        lines.append(f"{top['province']} Province had the most abnormal payments: {top['flagged']:,} of {totals['flagged']:,} ({top['share']}%).")
    if districts and districts[0]["flagged"]:
        lead = districts[0]
        lines.append(f"{lead['district']} has the most abnormal payments of any district: {lead['flagged']} of {lead['payments']:,} ({lead['rate']}%).")
    for spot in hotspots[:3]:
        lines.append(f"{spot['district']} runs {spot['multiple']}x the national flag rate ({spot['rate']}% against {nat_rate}%).")
    for riser in rising[:2]:
        lines.append(
            f"{riser['district']} had {riser['last7_flagged']} abnormal payments in the last 7 days, "
            f"against about {riser['expected']} expected from its usual rate of {riser['usual_rate']}%."
        )
    if trend["change_pct"] is not None:
        direction = "up" if trend["change_pct"] > 0 else "down" if trend["change_pct"] < 0 else "level"
        if direction == "level":
            lines.append(f"Abnormal payments in the last 7 full days ({trend['last7_flagged']}) are level with the 7 days before.")
        else:
            lines.append(
                f"Abnormal payments in the last 7 full days ({trend['last7_flagged']}) are {direction} {abs(trend['change_pct'])}% on the 7 days before ({trend['prev7_flagged']})."
            )
    return "\n".join(lines), lines


_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {item.replace(",", "").rstrip(".") for item in _NUMBER.findall(text)}


def _brief(facts: str, findings: list[str]) -> tuple[str, str]:
    """The summary shown on the dashboard. Rules write it; a model may reword it, checked against the facts."""
    rules_text = " ".join(findings)
    key = hashlib.sha256(facts.encode()).hexdigest()
    if key in _brief_cache:
        return _brief_cache[key]
    answer = (rules_text, "rules")
    from app.runtime import runtime

    settings = runtime.settings
    if settings is not None and (settings.gemini_api_key or settings.meta_api_key):
        try:
            enforce("analytics", "write_brief")
            result = runtime.llm.complete(system=BRIEF_SYSTEM, user=facts, purpose="analytics")
            invented = _numbers(result.text) - _numbers(facts)
            if result.provider != "mock" and result.text.strip() and not invented:
                answer = (" ".join(result.text.split()), result.provider)
        except Exception:
            answer = (rules_text, "rules")
    if len(_brief_cache) > 64:
        _brief_cache.clear()
    _brief_cache[key] = answer
    return answer
