from dataclasses import dataclass, field


@dataclass
class Signals:
    sim_swap_age_hours: float | None = None
    new_imei_age_hours: float | None = None
    location_change_age_hours: float | None = None
    location_note: str | None = None
    port_out: bool = False
    new_payee: bool = False
    amount_lkr: int = 0
    password_reset_age_hours: float | None = None
    new_device_login: bool = False
    latest_sim_change: str | None = None
    swapped: bool = False
    # From the customer's own ledger (app/profile.py). Both stay False when there is too little history.
    familiar_payment: bool = False
    unusual_amount: bool = False


@dataclass
class PolicyResult:
    decision: str
    score: int
    factors: list = field(default_factory=list)
    reason: str = ""


# Numbers an analyst may tune. The defaults are the original rule table. Each has hard limits that no
# proposal and no approval can go outside. Everything else in decide_policy is fixed.
DEFAULTS = {"hold_score": 45}
SAFE_RANGE = {"hold_score": (35, 70)}
_thresholds = dict(DEFAULTS)


def thresholds() -> dict:
    return dict(_thresholds)


def set_threshold(key: str, value: int) -> None:
    low, high = SAFE_RANGE[key]
    if not low <= int(value) <= high:
        raise ValueError(f"{key} must stay between {low} and {high}")
    _thresholds[key] = int(value)


def reset_thresholds() -> None:
    _thresholds.clear()
    _thresholds.update(DEFAULTS)


def compute_risk_score(signals: Signals) -> tuple[int, list[dict]]:
    """Score only. The hold / block / release choice is decide_policy."""
    factors: list[dict] = []
    score = 0

    def add(points: int, code: str, label: str) -> None:
        nonlocal score
        score += points
        factors.append({"code": code, "label": label, "points": points})

    age = signals.sim_swap_age_hours
    if age is not None:
        if age <= 24:
            add(40, "sim_swap_24h", "SIM swap within 24 hours")
        elif age <= 24 * 7:
            add(25, "sim_swap_7d", "SIM swap within 7 days")
        elif age <= 24 * 10:
            add(12, "sim_swap_10d", "SIM swap within 10 days")
    if signals.new_imei_age_hours is not None and signals.new_imei_age_hours <= 72:
        add(15, "new_imei", "New handset (IMEI) in the last 72 hours")
    if signals.location_change_age_hours is not None and signals.location_change_age_hours <= 48:
        label = "Serving location changed in the last 48 hours"
        if signals.location_note:
            label = f"{label} ({signals.location_note})"
        add(10, "location", label)
    if signals.port_out:
        add(35, "port_out", "Port-out request is open")
    if signals.new_payee:
        add(15, "new_payee", "Payee was added recently")
    if signals.amount_lkr >= 500_000:
        add(25, "amount_500k", "Transfer is at least Rs. 500,000")
    elif signals.amount_lkr >= 100_000:
        add(20, "amount_100k", "Transfer is at least Rs. 100,000")
    elif signals.amount_lkr >= 50_000:
        add(8, "amount_50k", "Transfer is at least Rs. 50,000")
    if signals.password_reset_age_hours is not None and signals.password_reset_age_hours <= 12:
        add(15, "password_reset", "Password reset in the last 12 hours")
    if signals.new_device_login:
        add(10, "new_device_login", "Sign-in from a device not seen on this account")
    if signals.unusual_amount:
        add(10, "unusual_amount", "Amount is more than twice this customer's usual high, shortly after a SIM change")
    if signals.familiar_payment:
        add(-25, "familiar_payment", "Established payee and a usual amount for this customer")
    return max(0, min(score, 100)), factors


def decide_policy(signals: Signals) -> PolicyResult:
    """Deterministic rule layer. A model note must not change this result."""
    score, factors = compute_risk_score(signals)
    critical = (
        signals.port_out
        and signals.new_payee
        and signals.password_reset_age_hours is not None
        and signals.password_reset_age_hours <= 6
        and signals.amount_lkr >= 500_000
    )
    if critical:
        return PolicyResult(
            decision="block",
            score=score,
            factors=factors,
            reason=(
                "Rule: an open port-out, a password reset within 6 hours, a new payee, "
                "and a transfer of at least Rs. 500,000. The transfer is blocked without waiting."
            ),
        )
    recent_swap = signals.sim_swap_age_hours is not None and signals.sim_swap_age_hours <= 72
    correlated = recent_swap and signals.amount_lkr >= 50_000 and (
        signals.new_payee
        or signals.port_out
        or signals.new_device_login
        or (
            signals.password_reset_age_hours is not None
            and signals.password_reset_age_hours <= 12
        )
    )
    if score >= _thresholds["hold_score"] or correlated:
        return PolicyResult(
            decision="hold",
            score=score,
            factors=factors,
            reason=(
                "Rule: the transfer is suspicious next to a recent SIM change, so the money "
                "stays held until the customer answers on their registered device."
            ),
        )
    return PolicyResult(
        decision="release",
        score=score,
        factors=factors,
        reason=(
            "Rule: the signals do not meet the hold threshold. The transfer can proceed. "
            "A short case note is still filed."
        ),
    )
