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
