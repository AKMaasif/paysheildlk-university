"""Simulated failures for emergency drills.

A drill flips one of these switches and the real code path reacts: the model
client falls back, the SMS or email sender fails, or an agent crashes. Nothing
here is a pretend message. Switches live in memory, are off by default and
reset on restart, so a forgotten drill cannot outlive the process. Turning one
on or off is an operator action and is written to the audit log by the caller.
"""

import threading

_lock = threading.Lock()
_active: dict[str, str] = {}

# name -> what it simulates
KNOWN: dict[str, str] = {
    "llm_down": "Meta and the Grok fallback are both unreachable",
    "sms_down": "The SMS gateway is failing",
    "email_down": "The email server is failing",
    "fail_agent": "One agent crashes the next time it runs",
}


def turn_on(name: str, detail: str = "on") -> None:
    if name not in KNOWN:
        raise KeyError(name)
    with _lock:
        _active[name] = detail


def turn_off(name: str) -> None:
    with _lock:
        _active.pop(name, None)


def clear_all() -> list[str]:
    with _lock:
        names = sorted(_active)
        _active.clear()
        return names


def is_on(name: str) -> bool:
    with _lock:
        return name in _active


def snapshot() -> dict[str, dict]:
    with _lock:
        return {name: {"detail": detail, "meaning": KNOWN.get(name, "")} for name, detail in sorted(_active.items())}


def take_agent_failure(agent: str) -> bool:
    """True once if a crash was requested for this agent. The switch then turns itself off."""
    with _lock:
        if _active.get("fail_agent") == agent:
            _active.pop("fail_agent", None)
            return True
        return False


class SimulatedFailure(RuntimeError):
    """Raised inside an agent that a drill told to crash."""
