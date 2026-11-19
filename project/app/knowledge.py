import random

from app.answers import verify_secret
from app.security import answers_match


def draw_round(questions: list[dict], count: int = 3, rng: random.Random | None = None) -> list[dict]:
    pool = list(questions)
    picker = rng or random.SystemRandom()
    picker.shuffle(pool)
    chosen = pool[: min(count, len(pool))]
    return [{"id": item["id"], "prompt": item["prompt"]} for item in chosen]


def grade_round(questions: list[dict], answers: dict, pepper: str) -> bool:
    """True only when every answer matches. Callers must not say which one failed."""
    if not questions:
        return False
    for item in questions:
        given = (answers or {}).get(item["id"], "")
        stored = item.get("hash") or ""
        code = item.get("id") or ""
        if stored.startswith("pbkdf2$"):
            ok = verify_secret(given, stored, code)
        else:
            ok = answers_match(given, stored, pepper)
        if not ok:
            return False
    return True
