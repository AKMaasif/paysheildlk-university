"""Small sliding-window limiter for sign-in, authenticator codes and step-up answers.

Counts live in process memory. That is enough for the single-process pilot; a
multi-process deployment would move the same windows into Redis.
"""

import threading
import time
from collections import deque

from fastapi import HTTPException

# bucket -> (attempts allowed, window in seconds)
LIMITS: dict[str, tuple[int, int]] = {
    # Failed passwords per account, and all attempts per client address.
    "login_fail": (5, 15 * 60),
    "login_ip": (30, 15 * 60),
    # Failed authenticator codes per account. Six digits must not be guessable.
    "totp_fail": (5, 10 * 60),
    # Step-up submissions per case. The graph itself stops after four rounds or two fake selfies.
    "verify": (12, 10 * 60),
    "liveness": (8, 10 * 60),
    "refresh": (60, 15 * 60),
    # Registration: tries per client address, and failed matches per account number.
    "register_ip": (12, 15 * 60),
    "register_fail": (6, 15 * 60),
    "pw_change": (6, 15 * 60),
}

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}


def _window(bucket: str, key: str) -> deque:
    return _hits.setdefault((bucket, key), deque())


def _prune(hits: deque, span: int, now: float) -> None:
    while hits and now - hits[0] > span:
        hits.popleft()


def retry_after(bucket: str, key: str) -> int:
    """Seconds until the next attempt is allowed, or 0 when the bucket has room."""
    allowed, span = LIMITS[bucket]
    now = time.monotonic()
    with _lock:
        hits = _window(bucket, key)
        _prune(hits, span, now)
        if len(hits) < allowed:
            return 0
        return max(1, int(span - (now - hits[0])) + 1)


def check(bucket: str, key: str) -> None:
    """Raise 429 when the bucket is full. Does not count an attempt."""
    wait = retry_after(bucket, key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {max(1, wait // 60)} minute(s).",
            headers={"Retry-After": str(wait)},
        )


def hit(bucket: str, key: str) -> None:
    allowed, span = LIMITS[bucket]
    now = time.monotonic()
    with _lock:
        hits = _window(bucket, key)
        _prune(hits, span, now)
        hits.append(now)


def take(bucket: str, key: str) -> None:
    """Check, then count this attempt."""
    check(bucket, key)
    hit(bucket, key)


def clear(bucket: str, key: str) -> None:
    with _lock:
        _hits.pop((bucket, key), None)


def reset_all() -> None:
    with _lock:
        _hits.clear()
