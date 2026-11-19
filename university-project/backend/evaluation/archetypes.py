"""Synthetic payment scenarios with a ground-truth label.

The label (fraud or not) comes from how each scenario was built, not from the policy rules. That is
what makes the results meaningful: the rules can be wrong about a scenario, and the harness counts it.
Everything is synthetic. Nothing here says how the rules would perform on real customers.

Three groups:
  benign       ordinary and awkward-but-legitimate behaviour
  attack       the takeover patterns the rules were written for
  adversarial  the red-team generator: attacks shaped to slip under the thresholds
"""

import random
from dataclasses import dataclass, field


@dataclass
class Scenario:
    archetype: str
    group: str  # benign | attack | adversarial
    fraud: bool
    amount: int
    payee: str
    new_payee: bool
    swap_h: float | None = None
    imei_h: float | None = None
    loc_h: float | None = None
    port_out: bool = False
    reset_h: float | None = None
    new_device: bool = False
    # (payee, amount, days_ago) of earlier completed payments on the account
    history: list = field(default_factory=list)
    note: str = ""


BILLS = ["CEB Electricity", "Dialog Axiata", "Water Board", "Keells", "Lanka Gas", "Hospital Pharmacy"]
PEOPLE = ["R. Jayawardena", "S. Kumar Traders", "Perera and Sons", "Galle Tuition Centre", "Ocean Hardware", "K.D. Enterprises"]


def _r(rng: random.Random, low: float, high: float, step: int = 50) -> int:
    return int(round(rng.uniform(low, high) / step) * step)


def _history(rng: random.Random, payee: str, amount: int, count: int = 3) -> list:
    return [(payee, max(500, int(amount * rng.uniform(0.8, 1.2))), rng.uniform(8, 80)) for _ in range(count)]


def _other_history(rng: random.Random, count: int = 6) -> list:
    """Earlier bill payments. Six, because the amount rule needs at least five earlier payments to fire.
    (A first run used three and the rule never fired; see docs/evaluation/NOTES.md.)"""
    return [(rng.choice(BILLS), _r(rng, 1500, 20000), rng.uniform(5, 60)) for _ in range(count)]


# ---------------- benign

def routine_bill(rng):
    payee, amount = rng.choice(BILLS), _r(rng, 1500, 25000)
    return Scenario("routine_bill", "benign", False, amount, payee, False, history=_history(rng, payee, amount))


def large_known_payee(rng):
    payee, amount = rng.choice(PEOPLE), _r(rng, 60000, 300000, 1000)
    return Scenario("large_known_payee", "benign", False, amount, payee, False, history=_history(rng, payee, amount))


def legit_new_payee(rng):
    return Scenario(
        "legit_new_payee", "benign", False, _r(rng, 40000, 350000, 1000), rng.choice(PEOPLE), True,
        new_device=rng.random() < 0.3, history=_other_history(rng), note="paying a new tutor or supplier",
    )


def legit_new_phone(rng):
    payee, amount = rng.choice(BILLS), _r(rng, 2000, 40000)
    swap = rng.uniform(2, 60)
    return Scenario(
        "legit_new_phone", "benign", False, amount, payee, False, swap_h=swap, imei_h=max(0.5, swap - rng.uniform(0, 1)),
        history=_history(rng, payee, amount), note="customer replaced their phone and SIM",
    )


def legit_travel(rng):
    payee, amount = rng.choice(BILLS), _r(rng, 2000, 30000)
    return Scenario("legit_travel", "benign", False, amount, payee, False, loc_h=rng.uniform(1, 40), history=_history(rng, payee, amount))


def legit_swap_new_payee(rng):
    return Scenario(
        "legit_swap_new_payee", "benign", False, _r(rng, 55000, 120000, 1000), rng.choice(PEOPLE), True,
        swap_h=rng.uniform(30, 70), imei_h=rng.uniform(30, 70), history=_other_history(rng), note="new phone, then a genuine new payee",
    )


def legit_rent_after_new_phone(rng):
    payee, amount = rng.choice(PEOPLE), _r(rng, 60000, 120000, 1000)
    swap = rng.uniform(1, 20)
    return Scenario(
        "legit_rent_after_new_phone", "benign", False, amount, payee, False, swap_h=swap, imei_h=max(0.5, swap - 0.5),
        history=[(payee, int(amount * rng.uniform(0.9, 1.1)), 35 - 8 * i) for i in range(4)], note="new phone, then the usual rent",
    )


# ---------------- attack

def classic_takeover(rng):
    swap = rng.uniform(0.1, 6)
    return Scenario(
        "classic_takeover", "attack", True, _r(rng, 80000, 600000, 1000), rng.choice(PEOPLE), True, swap_h=swap,
        imei_h=swap if rng.random() < 0.7 else None, loc_h=rng.uniform(0, 6) if rng.random() < 0.5 else None,
        new_device=rng.random() < 0.6, history=_other_history(rng),
    )


def port_out_takeover(rng):
    return Scenario(
        "port_out_takeover", "attack", True, _r(rng, 500000, 1500000, 5000), rng.choice(PEOPLE), True, swap_h=rng.uniform(0.5, 5),
        port_out=True, reset_h=rng.uniform(0.2, 5), new_device=rng.random() < 0.7, history=_other_history(rng),
    )


def mid_size_takeover(rng):
    return Scenario(
        "mid_size_takeover", "attack", True, _r(rng, 50000, 99000, 500), rng.choice(PEOPLE), True, swap_h=rng.uniform(0.5, 20),
        reset_h=rng.uniform(0.5, 10) if rng.random() < 0.4 else None, history=_other_history(rng),
    )


# ---------------- adversarial (red team): attacks shaped to stay under the thresholds

def sub_threshold(rng):
    return Scenario(
        "sub_threshold", "adversarial", True, _r(rng, 20000, 49000, 500), rng.choice(PEOPLE), True, swap_h=rng.uniform(26, 70),
        history=_other_history(rng), note="swap older than a day, amount under Rs. 50,000",
    )


def stale_swap(rng):
    return Scenario(
        "stale_swap", "adversarial", True, _r(rng, 50000, 95000, 500), rng.choice(PEOPLE), True, swap_h=rng.uniform(190, 235),
        history=_other_history(rng), note="attacker waits 8 to 10 days after the swap",
    )


def split_payment(rng):
    return Scenario(
        "split_payment", "adversarial", True, _r(rng, 42000, 48000, 500), rng.choice(PEOPLE), True, swap_h=rng.uniform(26, 70),
        history=_other_history(rng), note="one of several payments, each just under Rs. 50,000",
    )


def mule_already_added(rng):
    payee = rng.choice(PEOPLE)
    return Scenario(
        "mule_already_added", "adversarial", True, _r(rng, 120000, 400000, 1000), payee, False, swap_h=rng.uniform(1, 10),
        history=[(payee, 3000, 3.0)] + _other_history(rng), note="mule payee added earlier, so the payee is not new",
    )


def patient_mule(rng):
    payee = rng.choice(PEOPLE)
    return Scenario(
        "patient_mule", "adversarial", True, _r(rng, 150000, 400000, 1000), payee, False, swap_h=rng.uniform(1, 10),
        history=[(payee, _r(rng, 3000, 8000), 40.0), (payee, _r(rng, 3000, 8000), 22.0)] + _other_history(rng),
        note="mule paid small amounts weeks ago, so the payee looks established",
    )


def low_and_slow(rng):
    return Scenario(
        "low_and_slow", "adversarial", True, _r(rng, 1000, 3000, 100), rng.choice(PEOPLE), True, swap_h=rng.uniform(1, 6),
        history=_other_history(rng), note="small test payment right after the swap",
    )


ARCHETYPES = [
    routine_bill, large_known_payee, legit_new_payee, legit_new_phone, legit_travel, legit_swap_new_payee, legit_rent_after_new_phone,
    classic_takeover, port_out_takeover, mid_size_takeover,
    sub_threshold, stale_swap, split_payment, mule_already_added, patient_mule, low_and_slow,
]


def generate(per_archetype: int, seed: int) -> list[Scenario]:
    rng = random.Random(seed)
    out: list[Scenario] = []
    for build in ARCHETYPES:
        for _ in range(per_archetype):
            out.append(build(rng))
    rng.shuffle(out)
    return out
