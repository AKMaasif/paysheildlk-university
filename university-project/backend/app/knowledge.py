import random

from app.answers import verify_secret


def draw_round(questions: list[dict], count: int = 3, rng: random.Random | None = None, avoid: set | None = None) -> list[dict]:
    """Three different questions in a random order. Questions already shown in an earlier round are used last, so a
    customer with enough questions never sees the same one twice before the pool runs out."""
    picker = rng or random.SystemRandom()
    fresh = [item for item in questions if item["id"] not in (avoid or set())]
    stale = [item for item in questions if item["id"] in (avoid or set())]
    picker.shuffle(fresh)
    picker.shuffle(stale)
    chosen = (fresh + stale)[: min(count, len(questions))]
    return [{"id": item["id"], "prompt": item["prompt"]} for item in chosen]


def grade_round(questions: list[dict], answers: dict, pepper: str = "") -> bool:
    """True only when every answer matches. Callers must not say which one failed."""
    if not questions:
        return False
    for item in questions:
        given = (answers or {}).get(item["id"], "")
        if not verify_secret(given, item.get("hash") or "", item.get("id") or "", pepper):
            return False
    return True
