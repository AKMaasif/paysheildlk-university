"""The signals a decision rests on, read once, with where each one came from.

Before this module the score and the policy decision each read the records on their own, a moment apart, and the Telecom
agent's answer from a mobile operator was never what the policy used. Now a decision is made on a **snapshot**:

* one reading of every signal, taken once and stored with the case;
* for each signal its source, when it happened (observed), when this system took it in (received), and when it was read
  (fetched), so a delayed or old observation can be seen as delayed or old;
* a digest of the facts behind it: the same facts always give the same digest, however much time has passed;
* the score and the rule decision are both worked out from that stored snapshot, so they cannot disagree with each other;
* a later snapshot is taken again before money is released. If it shows something materially worse, the money is not
  released. The earlier decision is never edited: it keeps pointing at the snapshot it used.

When an operator gateway is configured (external mode) its answer about a SIM swap is the authority. If the local record
says something different, the disagreement is written into the snapshot and shown, and the operator's answer is used. It is
never silently replaced by local data. If the gateway cannot be reached, there is no decision on that signal: the case
fails safe with the money held.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from app.db import session_scope
from app.facts import read_signals
from app.models import DecisionSnapshot
from app.risk import Signals, compute_risk_score, decide_policy
from app.util import age_hours, iso, nid, utcnow

CONTRACT_VERSION = 1
RANK = {"release": 0, "hold": 1, "block": 2}
# Warning signs that must not be ignored just because an earlier check passed.
HIGH_RISK_CODES = {"port_out", "sim_swap_24h", "password_reset", "new_device_login", "new_imei", "unusual_amount"}
SCORE_JUMP = 15  # a rise this big between two snapshots is new information in itself


class TelecomUnavailable(RuntimeError):
    """The operator gateway is configured and did not answer. There is no safe local substitute."""


@dataclass
class Snapshot:
    id: str
    case_id: str | None
    purpose: str
    version: int
    created_at: str
    signals: dict
    provenance: dict
    conflicts: list = field(default_factory=list)
    digest: str = ""

    def as_signals(self) -> Signals:
        return Signals(**self.signals)

    def describe(self) -> dict:
        return {"id": self.id, "purpose": self.purpose, "version": self.version, "digest": self.digest[:16], "created_at": self.created_at, "conflicts": self.conflicts,
                "sim_swap_source": self.provenance.get("sim_swap", {}).get("source")}


def _digest(basis: dict) -> str:
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def _basis(signals: Signals, observed: dict) -> dict:
    """The facts behind a reading, as absolute times and flags. Ages are left out: they only grow as the clock runs."""
    return {
        "contract": CONTRACT_VERSION,
        "sim_swap": [observed["sim_swap"].get("occurred_at"), observed["sim_swap"].get("source")],
        "new_imei": observed["new_imei"].get("occurred_at"),
        "location_change": observed["location_change"].get("occurred_at"),
        "port_out": [observed["port_out"].get("occurred_at"), signals.port_out],
        "password_reset": observed["password_reset"].get("occurred_at"),
        "new_device_login": signals.new_device_login,
        "new_payee": signals.new_payee,
        "amount_lkr": signals.amount_lkr,
        "familiar_payment": signals.familiar_payment,
        "unusual_amount": signals.unusual_amount,
    }


def remote_sim_swap(phone: str) -> dict:
    """Ask the operator gateway now. Raises TelecomUnavailable if it does not answer."""
    from app.agents.tools import _remote_sim_swap

    try:
        got = _remote_sim_swap(phone, 240)
    except Exception as exc:
        raise TelecomUnavailable(f"The operator gateway did not answer ({type(exc).__name__}).") from exc
    return {
        "source": "camara-http",
        "swapped": bool((got.get("checkSimSwap") or {}).get("swapped")),
        "latest_sim_change": (got.get("retrieveSimSwapDate") or {}).get("latestSimChange"),
        "fetched_at": iso(utcnow()),
    }


def build(customer_id: str, transaction_id: str, *, case_id: str | None, purpose: str, telecom: dict | None = None) -> Snapshot:
    """Read everything once and store the reading. `telecom` is the answer the Telecom agent itself got in this run, so the
    score uses the very observation the agent reported instead of asking again."""
    from app.runtime import runtime

    signals, context, observed = read_signals(customer_id, transaction_id)
    now = utcnow()
    conflicts: list[dict] = []
    local = {"swapped": signals.swapped, "latest_sim_change": signals.latest_sim_change}
    sim = dict(observed["sim_swap"], source="local-db")

    if runtime.settings is not None and runtime.settings.telco_base_url:
        remote = telecom if telecom and telecom.get("source") == "camara-http" else remote_sim_swap(context["phone"])
        latest = remote.get("latest_sim_change")
        if remote.get("swapped"):
            age = age_hours(_parse(latest), now) if latest else 0.0
            signals.sim_swap_age_hours, signals.swapped, signals.latest_sim_change = (0.0 if age is None or age < 0 else age), True, latest
        else:
            signals.sim_swap_age_hours, signals.swapped, signals.latest_sim_change = None, False, None
        sim = {"event_id": None, "occurred_at": latest, "received_at": remote.get("fetched_at"), "origin": "operator gateway", "source": "camara-http", "fetched_at": remote.get("fetched_at")}
        if local["swapped"] != signals.swapped:
            conflicts.append({"signal": "sim_swap", "kind": "operator_and_local_record_disagree", "used": "operator gateway",
                              "operator_said_swapped": signals.swapped, "local_record_said_swapped": local["swapped"]})
    elif telecom and telecom.get("source") == "camara-mock" and bool(telecom.get("swapped")) != signals.swapped:
        conflicts.append({"signal": "sim_swap", "kind": "changed_since_the_telecom_agent_read", "used": "the newer local record",
                          "telecom_agent_saw_swapped": bool(telecom.get("swapped")), "now_swapped": signals.swapped})

    observed["sim_swap"] = sim
    for key in ("sim_swap", "new_imei", "location_change", "port_out"):
        item = observed[key]
        occurred, received = item.get("occurred_at"), item.get("received_at")
        item["age_seconds"] = None if not occurred else int((now - _parse(occurred)).total_seconds())
        item["ingestion_lag_seconds"] = None if not (occurred and received) else int((_parse(received) - _parse(occurred)).total_seconds())
    observed["fetched_at"] = iso(now)
    observed["contract_version"] = CONTRACT_VERSION

    snapshot = Snapshot(
        id=nid("snap"), case_id=case_id, purpose=purpose, version=1, created_at=iso(now),
        signals=asdict(signals), provenance=observed, conflicts=conflicts, digest=_digest(_basis(signals, observed)),
    )
    if case_id:
        with session_scope() as session:
            snapshot.version = session.query(DecisionSnapshot).filter(DecisionSnapshot.case_id == case_id).count() + 1
            score, factors = compute_risk_score(signals)
            session.add(DecisionSnapshot(
                id=snapshot.id, case_id=case_id, purpose=purpose, version=snapshot.version, created_at=now, signals=snapshot.signals,
                provenance=snapshot.provenance, conflicts=conflicts, digest=snapshot.digest, score=score, decision=decide_policy(signals).decision,
            ))
    return snapshot


def _parse(value: str):
    from datetime import datetime, timezone

    text = str(value).replace(" ", "T").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load(snapshot_id: str) -> Snapshot | None:
    with session_scope() as session:
        row = session.get(DecisionSnapshot, snapshot_id)
        if row is None:
            return None
        return Snapshot(id=row.id, case_id=row.case_id, purpose=row.purpose, version=row.version, created_at=iso(row.created_at), signals=dict(row.signals),
                        provenance=dict(row.provenance), conflicts=list(row.conflicts or []), digest=row.digest)


def record_decision(snapshot_id: str, decision: str, score: int) -> None:
    """The final decision, with emergency and account-limit rules applied, written beside the snapshot it was made on."""
    with session_scope() as session:
        row = session.get(DecisionSnapshot, snapshot_id)
        if row is not None:
            row.decision, row.score = decision, score


def material_changes(prior: Snapshot | None, new: Snapshot) -> list[str]:
    """What is worse in `new` than in `prior`, in words. Empty means nothing material has changed.

    Three things count: the rules now say something stricter, a high-risk warning sign has appeared that was not there,
    or the score has risen by a lot. Getting better, or changing only a little, is not a reason to stop."""
    new_signals = new.as_signals()
    new_score, new_factors = compute_risk_score(new_signals)
    new_decision = decide_policy(new_signals).decision
    if prior is None:
        return [] if new_decision == "release" else [f"the rules now say {new_decision} and there is no earlier reading to compare with"]
    old_signals = prior.as_signals()
    old_score, old_factors = compute_risk_score(old_signals)
    old_decision = decide_policy(old_signals).decision
    reasons: list[str] = []
    if RANK[new_decision] > RANK[old_decision]:
        reasons.append(f"the rules now say {new_decision}, they said {old_decision}")
    old_codes = {f["code"] for f in old_factors}
    for factor in new_factors:
        if factor["code"] in HIGH_RISK_CODES and factor["code"] not in old_codes:
            reasons.append(f"new warning sign: {factor['label']}")
    if new_score - old_score >= SCORE_JUMP:
        reasons.append(f"the score rose from {old_score} to {new_score}")
    return reasons

