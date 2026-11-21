"""The feedback loop, with a person in it.

What the Analytics agent can learn: for payments the rules held, the customer's own answer says whether
the hold was right. "It was not me" confirms fraud. "It was me" and a pass on the security questions
shows a false hold. From those answers it works out how often holds in each score band were right.

What it can do about it: write a proposal. A proposal changes nothing. An analyst reads the evidence and
approves or rejects it. Approval is the only thing that moves the number, and the number has hard limits.

What it cannot learn: how much fraud the rules released, because nobody tells the system about a
payment that was released and later turned out to be fraud. So it can only ever propose raising the
hold score, only when the lowest band has no confirmed fraud at all, and the page says what that would
release.
"""

from app.charters import enforce
from app.models import Case, PolicyProposal, PolicySetting
from app.risk import DEFAULTS, SAFE_RANGE, set_threshold, thresholds
from app.util import iso, nid, utcnow

BAND_WIDTH = 10
MIN_CASES = 20  # held cases in the lowest band before a proposal is even considered
STEP = 10


def load_thresholds(session) -> None:
    from app.risk import reset_thresholds

    reset_thresholds()
    for row in session.query(PolicySetting).all():
        if row.key in DEFAULTS:
            try:
                set_threshold(row.key, row.value)
            except ValueError:
                continue


def _outcome(case: Case) -> str | None:
    """fraud, legitimate, or None while the answer is not in."""
    if case.customer_decision == "no":
        return "fraud"
    if case.customer_decision == "yes":
        if case.verification_passed is True:
            return "legitimate"
        if case.verification_passed is False:
            return "fraud"  # said yes, then failed the questions
    return None


def evidence(session) -> dict:
    """Held cases at or above the hold score, grouped into score bands, with how many holds were right."""
    hold = thresholds()["hold_score"]
    rows = session.query(Case).filter(Case.policy_decision == "hold", Case.risk_score >= hold).all()
    bands: dict[int, dict] = {}
    for case in rows:
        outcome = _outcome(case)
        if outcome is None:
            continue
        start = hold + ((case.risk_score - hold) // BAND_WIDTH) * BAND_WIDTH
        band = bands.setdefault(start, {"from": start, "to": min(start + BAND_WIDTH - 1, 100), "fraud": 0, "legitimate": 0})
        band[outcome] += 1
    table = []
    for start in sorted(bands):
        b = bands[start]
        n = b["fraud"] + b["legitimate"]
        table.append({**b, "cases": n, "hold_was_right": round(b["fraud"] / n, 3) if n else None})
    return {"hold_score": hold, "bands": table, "answered_cases": sum(b["cases"] for b in table)}


def propose(session) -> list[dict]:
    """The Analytics agent's suggestion, if the evidence supports one. Writes a pending proposal."""
    enforce("analytics", "propose_threshold")
    ev = evidence(session)
    current = ev["hold_score"]
    low, high = SAFE_RANGE["hold_score"]
    if not ev["bands"] or ev["bands"][0]["from"] != current:
        return []
    first = ev["bands"][0]
    proposed = current + STEP
    if first["cases"] < MIN_CASES or first["fraud"] > 0 or proposed > high or proposed < low:
        return []
    exists = session.query(PolicyProposal).filter(PolicyProposal.key == "hold_score", PolicyProposal.status == "pending", PolicyProposal.proposed == proposed).first()
    if exists is not None:
        return []
    reason = (
        f"Of {first['cases']} held payments scored {first['from']} to {first['to']}, the customer confirmed all {first['legitimate']} as theirs "
        f"and none was fraud. Raising the hold score from {current} to {proposed} would release payments in that band without asking. "
        "The system cannot see fraud that was released and found later, so this is evidence about false holds only."
    )
    row = PolicyProposal(
        id=nid("pol"), key="hold_score", current=current, proposed=proposed, reason=reason,
        evidence={"band": first, "min_cases": MIN_CASES}, status="pending", created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return [proposal_dict(row)]


def proposal_dict(row: PolicyProposal) -> dict:
    return {
        "id": row.id, "key": row.key, "current": row.current, "proposed": row.proposed, "reason": row.reason, "evidence": row.evidence,
        "status": row.status, "created_at": iso(row.created_at), "decided_by": row.decided_by,
        "decided_at": iso(row.decided_at) if row.decided_at else None, "decision_note": row.decision_note,
    }


def decide(session, proposal_id: str, approve: bool, actor_email: str, note: str) -> dict:
    """The human gate. Approval is the only way a threshold moves."""
    row = session.get(PolicyProposal, proposal_id)
    if row is None:
        raise LookupError(proposal_id)
    if row.status != "pending":
        raise ValueError("That proposal has already been decided.")
    if approve:
        if row.current != thresholds()["hold_score"]:
            row.status = "superseded"
            session.commit()  # keep this even though the caller is about to see an error and roll back
            raise ValueError("The hold score has changed since this was proposed. Ask for a fresh proposal.")
        set_threshold(row.key, row.proposed)  # raises if outside the hard limits
        setting = session.get(PolicySetting, row.key)
        if setting is None:
            session.add(PolicySetting(key=row.key, value=row.proposed, updated_by=actor_email, updated_at=utcnow()))
        else:
            setting.value, setting.updated_by, setting.updated_at = row.proposed, actor_email, utcnow()
    row.status = "approved" if approve else "rejected"
    row.decided_by, row.decided_at, row.decision_note = actor_email, utcnow(), note[:500]
    return proposal_dict(row)


def reset(session, actor_email: str) -> None:
    from app.risk import reset_thresholds

    reset_thresholds()
    for row in session.query(PolicySetting).all():
        session.delete(row)
