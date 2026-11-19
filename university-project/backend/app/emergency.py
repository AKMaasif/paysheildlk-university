"""District emergency mode.

When a district sees a sudden run of held or blocked payments, or an analyst
switches it on, payments there at or above ``HOLD_ABOVE_LKR`` are held for the
customer's confirmation even if the normal rules would release them. The check
runs inside the policy step (see ``tools.apply_policy``). It can only make a
release into a hold. It never releases, and never lowers a hold or a block.
"""

from datetime import timedelta

from app.db import session_scope
from app.models import EmergencyMode, Transaction, User
from app.util import aware, utcnow

# This many held or blocked payments in a district inside the window puts the
# district into emergency hold for anything at or above the amount.
SPIKE_COUNT = 8
SPIKE_WINDOW_MINUTES = 10
HOLD_ABOVE_LKR = 50_000


def force_emergency(district: str, reason: str, by: str = "system") -> dict:
    with session_scope() as session:
        row = session.get(EmergencyMode, district)
        if row is None:
            session.add(EmergencyMode(district=district, reason=reason[:200], set_by=by[:255], set_at=utcnow()))
        else:
            row.reason, row.set_by, row.set_at = reason[:200], by[:255], utcnow()
    return breaker_status(district)


def clear_emergency(district: str) -> dict:
    with session_scope() as session:
        row = session.get(EmergencyMode, district)
        if row is not None:
            session.delete(row)
    return breaker_status(district)


def clear_all() -> int:
    with session_scope() as session:
        return session.query(EmergencyMode).delete()


def _spike_counts() -> dict[str, int]:
    """Held or blocked live payments per district inside the window.
    The simulated analytics book is history, so it never counts toward a live spike."""
    window = aware(utcnow()) - timedelta(minutes=SPIKE_WINDOW_MINUTES)
    with session_scope() as session:
        district_of = {row.id: row.district for row in session.query(User.id, User.district)}
        rows = (
            session.query(Transaction)
            .filter(Transaction.status.in_(["held", "blocked"]))
            .filter(Transaction.occurred_at >= window)
            .filter(Transaction.origin != "simulated")
            .all()
        )
        counts: dict[str, int] = {}
        for row in rows:
            name = (row.detail or {}).get("district") or district_of.get(row.customer_id) or ""
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def breaker_status(district: str | None = None) -> dict:
    counts = _spike_counts()
    active = {name: f"fraud spike: {count} held or blocked in {SPIKE_WINDOW_MINUTES} minutes" for name, count in counts.items() if count >= SPIKE_COUNT}
    with session_scope() as session:
        for row in session.query(EmergencyMode).all():
            active[row.district] = f"{row.reason} (set by {row.set_by})"
    return {
        "active": active,
        "tripped": bool(district and district in active),
        "hold_above_lkr": HOLD_ABOVE_LKR,
        "window_minutes": SPIKE_WINDOW_MINUTES,
        "threshold": SPIKE_COUNT,
        "recent_counts": counts,
    }


def must_hold(district: str | None, amount_lkr: int) -> str | None:
    """Why this payment must be held under emergency mode, or None."""
    if not district or amount_lkr < HOLD_ABOVE_LKR:
        return None
    status = breaker_status(district)
    return status["active"].get(district) if status["tripped"] else None
