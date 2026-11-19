from collections import defaultdict


def _rate(a: int, b: int) -> float | None:
    return None if b == 0 else round(a / b, 4)


def confusion(rows: list[dict]) -> dict:
    """rows: {fraud: bool, decision: str}. Flagged means hold or block."""
    tp = sum(1 for r in rows if r["fraud"] and r["decision"] in {"hold", "block"})
    fn = sum(1 for r in rows if r["fraud"] and r["decision"] not in {"hold", "block"})
    fp = sum(1 for r in rows if not r["fraud"] and r["decision"] in {"hold", "block"})
    tn = sum(1 for r in rows if not r["fraud"] and r["decision"] not in {"hold", "block"})
    return {
        "n": len(rows), "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "accuracy": _rate(tp + tn, len(rows)),
        "precision": _rate(tp, tp + fp),
        "recall": _rate(tp, tp + fn),
        "false_hold_rate": _rate(fp, fp + tn),
        "missed_fraud_rate": _rate(fn, fn + tp),
    }


def by_archetype(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["archetype"]].append(r)
    out = []
    for name, items in groups.items():
        flagged = sum(1 for r in items if r["decision"] in {"hold", "block"})
        out.append({
            "archetype": name, "group": items[0]["group"], "fraud": items[0]["fraud"], "n": len(items), "flagged": flagged,
            "flag_rate": round(flagged / len(items), 3),
            "decisions": {d: sum(1 for r in items if r["decision"] == d) for d in ("release", "hold", "block")},
        })
    order = {"benign": 0, "attack": 1, "adversarial": 2}
    return sorted(out, key=lambda x: (order[x["group"]], x["archetype"]))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return round(ordered[k], 4)
