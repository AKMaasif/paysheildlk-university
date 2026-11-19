"""What is usual for this customer, worked out from their own ledger.

This is plain statistics over completed payments: which payees they have paid, how often and since
when, and how large their payments usually are. It is not machine learning and it learns nothing
beyond the ledger. It exists to cut false holds on ordinary behaviour and to notice an amount far above
what this customer normally sends.

Guards against being gamed:
  * A payee counts as established only after at least two completed payments, the first of them at
    least 14 days ago. A mule added days before the attack does not qualify.
  * The profile needs at least three earlier payments before it changes anything, and five before it
    can raise the score.
  * It never touches the critical block rule or the swap-plus-warning hold rule. It only moves the score.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

from app.util import aware

COLOMBO = ZoneInfo("Asia/Colombo")
SPENDING_KINDS = {"transfer", "bill", "purchase"}
LOOKBACK_DAYS = 180
ESTABLISHED_AFTER_DAYS = 14
MIN_PAYMENTS_TO_LOWER = 3
MIN_PAYMENTS_TO_RAISE = 5
USUAL_HIGH_MULTIPLE = 1.25  # an amount up to this many times the usual high still counts as usual
UNUSUAL_MULTIPLE = 2.0  # an amount above this many times the usual high is unusual


@dataclass
class Profile:
    payments: int = 0
    usual_high_lkr: int = 0  # 90th percentile of earlier payment amounts
    median_lkr: int = 0
    payees: dict = field(default_factory=dict)  # payee -> {"count": n, "first_days_ago": d}
    active_hours: list = field(default_factory=list)  # hours of the day (Sri Lanka time) with most payments

    def established(self, payee: str | None, now) -> bool:
        info = self.payees.get(payee or "")
        return bool(info) and info["count"] >= 2 and info["first_days_ago"] >= ESTABLISHED_AFTER_DAYS


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def build_profile(session, customer_id: str, now, exclude_transaction_id: str | None = None) -> Profile:
    from app.models import Transaction

    since = now - timedelta(days=LOOKBACK_DAYS)
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.customer_id == customer_id,
            Transaction.kind.in_(SPENDING_KINDS),
            Transaction.status.in_(["completed", "released"]),
            Transaction.occurred_at >= since,
        )
        .all()
    )
    rows = [r for r in rows if r.id != exclude_transaction_id and aware(r.occurred_at) < aware(now)]
    amounts = [int(r.amount_lkr or 0) for r in rows if (r.amount_lkr or 0) > 0]
    payees: dict = {}
    for r in rows:
        if not r.payee:
            continue
        days = (aware(now) - aware(r.occurred_at)).total_seconds() / 86400
        info = payees.setdefault(r.payee, {"count": 0, "first_days_ago": 0.0})
        info["count"] += 1
        info["first_days_ago"] = max(info["first_days_ago"], days)
    hours: dict = {}
    for r in rows:
        hour = aware(r.occurred_at).astimezone(COLOMBO).hour
        hours[hour] = hours.get(hour, 0) + 1
    busiest = [h for h, _n in sorted(hours.items(), key=lambda kv: -kv[1])[:3]]
    return Profile(
        payments=len(rows),
        usual_high_lkr=_percentile(amounts, 0.9),
        median_lkr=_percentile(amounts, 0.5),
        payees=payees,
        active_hours=sorted(busiest),
    )


def judge(profile: Profile, payee: str | None, amount_lkr: int, new_payee: bool, swap_recent: bool, now) -> dict:
    """The two things the score may use. Both default to False when there is not enough history."""
    familiar = unusual = False
    if profile.payments >= MIN_PAYMENTS_TO_LOWER and not new_payee and profile.established(payee, now):
        familiar = amount_lkr <= USUAL_HIGH_MULTIPLE * max(profile.usual_high_lkr, 1)
    if (
        profile.payments >= MIN_PAYMENTS_TO_RAISE
        and swap_recent
        and amount_lkr >= 20_000
        and amount_lkr > UNUSUAL_MULTIPLE * max(profile.usual_high_lkr, 1)
    ):
        unusual = True
    return {"familiar_payment": familiar, "unusual_amount": unusual}
