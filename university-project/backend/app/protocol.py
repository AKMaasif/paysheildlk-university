import hashlib
import hmac
import json
import time
from dataclasses import dataclass

_seen: dict[str, float] = {}
_TTL = 600


@dataclass
class Envelope:
    sender: str
    receiver: str
    case_id: str
    timestamp: int
    nonce: str
    payload: dict
    signature: str


def _body(sender: str, receiver: str, case_id: str, timestamp: int, nonce: str, payload: dict) -> bytes:
    packed = json.dumps(
        {
            "sender": sender,
            "receiver": receiver,
            "case_id": case_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return packed.encode()


def sign_envelope(key: str, sender: str, receiver: str, case_id: str, payload: dict, nonce: str, timestamp: int | None = None) -> Envelope:
    moment = int(time.time()) if timestamp is None else int(timestamp)
    digest = hmac.new(key.encode(), _body(sender, receiver, case_id, moment, nonce, payload), hashlib.sha256).hexdigest()
    return Envelope(sender, receiver, case_id, moment, nonce, payload, digest)


def verify_envelope(key: str, envelope: Envelope) -> None:
    expected = hmac.new(
        key.encode(),
        _body(envelope.sender, envelope.receiver, envelope.case_id, envelope.timestamp, envelope.nonce, envelope.payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, envelope.signature):
        raise ValueError("bad signature")
    now = time.time()
    stale = [item for item, seen in _seen.items() if now - seen > _TTL]
    for item in stale:
        _seen.pop(item, None)
    if envelope.nonce in _seen:
        raise ValueError("replayed nonce")
    _seen[envelope.nonce] = now


# Which graph step may hand a case to which. A message on any other route is refused.
ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("supervisor", "telecom"),
        ("supervisor", "banking"),
        ("telecom", "risk"),
        ("banking", "risk"),
        ("risk", "policy"),
        ("policy", "alert"),
        ("policy", "response"),
        ("policy", "release"),
        ("wait_customer", "verification_prep"),
        ("wait_customer", "response"),
        ("wait_customer", "escalate"),
        ("verify_wait", "release"),
        ("verify_wait", "response"),
        ("verify_wait", "escalate"),
        ("release", "report"),
        ("response", "report"),
        ("escalate", "report"),
    }
)

MAX_AGE_SECONDS = _TTL


class HandoffRejected(PermissionError):
    pass


def seal(key: str, sender: str, receiver: str, case_id: str, payload: dict) -> dict:
    """Sign a handoff. Returns a plain dict so the graph checkpointer can store it."""
    if (sender, receiver) not in ROUTES:
        raise HandoffRejected(f"{sender} may not hand off to {receiver}")
    import secrets

    envelope = sign_envelope(key, sender, receiver, case_id, payload, nonce=secrets.token_hex(12))
    return {
        "sender": envelope.sender,
        "receiver": envelope.receiver,
        "case_id": envelope.case_id,
        "timestamp": envelope.timestamp,
        "nonce": envelope.nonce,
        "payload": envelope.payload,
        "signature": envelope.signature,
    }


def open_sealed(key: str, sealed: dict, receiver: str, case_id: str) -> dict:
    """Check route, addressee, case, age, signature and replay. Returns the payload."""
    try:
        envelope = Envelope(**sealed)
    except TypeError as exc:
        raise HandoffRejected("malformed handoff") from exc
    if (envelope.sender, envelope.receiver) not in ROUTES:
        raise HandoffRejected(f"route {envelope.sender} -> {envelope.receiver} is not allowed")
    if envelope.receiver != receiver:
        raise HandoffRejected(f"handoff is addressed to {envelope.receiver}, not {receiver}")
    if envelope.case_id != case_id:
        raise HandoffRejected("handoff belongs to another case")
    if abs(time.time() - envelope.timestamp) > MAX_AGE_SECONDS:
        raise HandoffRejected("handoff is too old")
    try:
        verify_envelope(key, envelope)
    except ValueError as exc:
        raise HandoffRejected(str(exc)) from exc
    return envelope.payload
