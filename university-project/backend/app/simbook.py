"""The simulated payment book behind the fraud analytics.

Every row is labelled simulated and sits under a dedicated ``simulated``
customer id, so it never appears in a real customer's ledger or in what the
banking agent reads. The book has structure on purpose, so there is something
for the analytics agent to find:

* volume follows population, with fewer payments at weekends;
* most districts sit near a 3% flag rate, a few run higher all month;
* one district (Kandy) spikes in the last five days.

None of this is real fraud data.
"""

import random
from datetime import timedelta

from app.geo import DISTRICTS
from app.models import Transaction

BOOK_VERSION = 2
BOOK_CUSTOMER = "simulated"
DAYS = 30
SPIKE_DISTRICT = "Kandy"

# Flag-rate multipliers on the 2.8% base. Anything not listed gets a draw between 0.55 and 1.05.
STEADY_HOT = {"Gampaha": 1.7, "Colombo": 1.5, "Jaffna": 1.35, "Kurunegala": 1.2}
SPIKE_FACTOR = 5.0
SPIKE_DAYS = 5
BASE_FLAG_RATE = 0.028


def _is_current(session) -> bool:
    row = session.query(Transaction).filter(Transaction.origin == "simulated").first()
    return bool(row and (row.detail or {}).get("v") == BOOK_VERSION)


def refresh(session, now) -> bool:
    """Build the book if it is missing or built by an older version. Returns True when it changed."""
    if _is_current(session):
        return False
    session.query(Transaction).filter(Transaction.origin == "simulated").delete(synchronize_session=False)
    session.flush()
    session.add_all(build(now))
    return True


def build(now) -> list[Transaction]:
    rng = random.Random(2611)  # noqa: S311 - a fixed seed, so the simulated book is the same every time; not a security use
    names = list(DISTRICTS)
    weights = [DISTRICTS[name][1] for name in names]
    multiplier = {
        name: STEADY_HOT.get(name, round(rng.uniform(0.55, 1.05), 2)) for name in names
    }
    rows: list[Transaction] = []
    index = 0
    for age in range(DAYS - 1, -1, -1):
        day = now - timedelta(days=age)
        weekend = day.weekday() >= 5
        count = int(rng.gauss(58 if weekend else 92, 7))
        for _ in range(count):
            district = rng.choices(names, weights=weights)[0]
            # Business hours carry most payments.
            hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 3, 5, 8, 10, 10, 9, 9, 9, 9, 8, 7, 6, 5, 4, 3, 2, 2, 1])[0]
            moment = day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)
            if moment > now:
                moment = now - timedelta(minutes=rng.randint(1, 90))
            rate = BASE_FLAG_RATE * multiplier[district]
            if district == SPIKE_DISTRICT and age < SPIKE_DAYS:
                rate *= SPIKE_FACTOR
            flagged = rng.random() < rate
            if flagged:
                amount = rng.choice([76_000, 150_000, 210_000, 340_000, 480_000, 800_000])
                recent = (now - moment) < timedelta(hours=36)
                roll = rng.random()
                if recent and roll < 0.30:
                    status = "held"
                elif roll < 0.72:
                    status = "blocked"
                else:
                    status = "released"
            else:
                amount = rng.choice([1_200, 2_500, 4_850, 8_500, 12_000, 24_000, 45_000, 76_000])
                status = "completed"
            rows.append(
                Transaction(
                    id=f"SIMU-{index:05d}",
                    customer_id=BOOK_CUSTOMER,
                    kind="transfer",
                    amount_lkr=amount,
                    payee="Simulated payee",
                    new_payee=flagged,
                    status=status,
                    occurred_at=moment,
                    origin="simulated",
                    detail={"district": district, "label": "simulated", "flagged": flagged, "v": BOOK_VERSION},
                )
            )
            index += 1
    return rows
