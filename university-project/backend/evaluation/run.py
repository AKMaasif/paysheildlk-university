"""python -m evaluation.run --per-archetype 12 --seed 7 --out ../docs/evaluation

Runs everything in a temporary database. All data is synthetic."""

import argparse
import json
import platform
import time
from pathlib import Path


def run(per_archetype: int, seed: int, injection_scenarios: bool = True) -> dict:
    import random

    from app.runtime import runtime
    from evaluation import baseline, bench, harness, metrics
    from evaluation.archetypes import classic_takeover, generate, routine_bill
    from evaluation.injections import PAYLOADS

    started = time.time()
    harness.boot()
    scenarios = generate(per_archetype, seed)

    # ------------------------------------------------ the whole system, case by case
    rows, contexts, tool_calls, tool_violations, errors = [], [], 0, 0, 0
    with harness.model_probe() as seen:
        for sc in scenarios:
            r = harness.run_case(sc)
            calls, bad = harness.tool_audit(r["events"])
            tool_calls, tool_violations = tool_calls + calls, tool_violations + bad
            errors += int(r["decision"] is None)
            contexts.append(r["ctx"])
            rows.append({
                "archetype": sc.archetype, "group": sc.group, "fraud": sc.fraud, "decision": r["decision"] or "none", "score": r["score"],
                "latency_s": r["latency_s"], "status": r["status"], "case_id": r["case_id"], "note": sc.note, "amount": sc.amount,
            })
    leak_count = harness.leaks_in(seen, contexts)
    system = {
        "overall": metrics.confusion(rows),
        "by_group": {g: metrics.confusion([r for r in rows if r["group"] == g or (g == "all_fraud" and r["fraud"])]) for g in ("benign", "attack", "adversarial")},
        "by_archetype": metrics.by_archetype(rows),
        "latency_s": {"p50": metrics.percentile([r["latency_s"] for r in rows], 50), "p95": metrics.percentile([r["latency_s"] for r in rows], 95)},
        "model_texts_seen": len(seen), "model_texts_with_raw_identifiers": leak_count,
        "tool_calls": tool_calls, "tool_calls_outside_charter": tool_violations, "cases_with_no_decision": errors,
    }

    # ------------------------------------------------ each agent on its own
    closed = [r["case_id"] for r in rows if r["status"].startswith("closed_")][:8] or [r["case_id"] for r in rows][:8]
    agents = {
        "supervisor": bench.supervisor(), "telecom": bench.telecom(seed), "banking": bench.banking(seed), "risk": bench.risk(seed),
        "policy": bench.policy(), "alert": bench.alert(), "verification": bench.verification(), "response": bench.response(seed),
        "report": bench.report(closed) + bench.critic(), "analytics": bench.analytics(),
    }

    # ------------------------------------------------ single-agent baseline
    base_rows = []
    for sc in generate(per_archetype, seed):
        out = baseline.rules_single(sc)
        base_rows.append({"fraud": sc.fraud, "decision": out["decision"], "latency_s": out["latency_s"], "archetype": sc.archetype, "group": sc.group})
    agree = sum(1 for a, b in zip(rows, base_rows, strict=False) if a["decision"] == b["decision"])
    single = {
        "rules_single_agent": {
            "same_decision_as_multi_agent": f"{agree} of {len(rows)}",
            "overall": metrics.confusion(base_rows),
            "latency_s": {"p50": metrics.percentile([r["latency_s"] for r in base_rows], 50), "p95": metrics.percentile([r["latency_s"] for r in base_rows], 95)},
        },
        "model_single_agent": {"ran": False, "reason": "No GEMINI_API_KEY or META_API_KEY is set. The mock model has no judgement, so a result from it would be invented."},
        "reach": baseline.reach(),
    }
    if baseline.model_available():
        sample = generate(max(1, 60 // 14), seed)
        out_rows = [(sc, baseline.model_single(sc)) for sc in sample]
        single["model_single_agent"] = {
            "ran": True, "cases": len(out_rows),
            "overall": metrics.confusion([{"fraud": sc.fraud, "decision": o["decision"]} for sc, o in out_rows]),
            "latency_s": {"p50": metrics.percentile([o["latency_s"] for _sc, o in out_rows], 50)},
        }

    # ------------------------------------------------ what the customer profile changed (same scenarios, profile off and on)
    off_rows, on_rows = [], []
    for sc in generate(per_archetype, seed):
        off_rows.append({"fraud": sc.fraud, "archetype": sc.archetype, "group": sc.group, "decision": baseline.rules_single(sc, use_profile=False)["decision"]})
        on_rows.append({"fraud": sc.fraud, "archetype": sc.archetype, "group": sc.group, "decision": baseline.rules_single(sc, use_profile=True)["decision"]})
    profile_effect = {
        "off": metrics.confusion(off_rows), "on": metrics.confusion(on_rows),
        "by_archetype_off": metrics.by_archetype(off_rows), "by_archetype_on": metrics.by_archetype(on_rows),
    }

    # ------------------------------------------------ the feedback loop: customer answers, proposal, and what a raise would do
    from app import calibration
    from app.db import session_scope
    from app.models import Case
    from app.risk import reset_thresholds, set_threshold

    with session_scope() as session:
        for r in rows:
            if r["decision"] == "hold":
                case = session.get(Case, r["case_id"])
                # The customers in this harness do not answer, so their answers are simulated from the scenario label.
                case.customer_decision = "no" if r["fraud"] else "yes"
                case.verification_passed = None if r["fraud"] else True
    with session_scope() as session:
        evidence = calibration.evidence(session)
        proposals = calibration.propose(session)
    what_if = {}
    for threshold in (45, 55, 65):
        set_threshold("hold_score", threshold)
        try:
            what_if[str(threshold)] = metrics.confusion([
                {"fraud": sc.fraud, "decision": baseline.rules_single(sc, use_profile=True)["decision"]} for sc in generate(per_archetype, seed)
            ])
        finally:
            reset_thresholds()
    calibration_result = {
        "answers": "simulated from the scenario labels (fraud answers No, legitimate answers Yes and passes the questions)",
        "evidence": evidence, "proposals": proposals, "what_if_hold_score": what_if, "min_cases": calibration.MIN_CASES,
    }

    # ------------------------------------------------ the same lookups through MCP
    mcp_effect: dict = {"ran": False, "reason": "the mcp package is not installed"}
    try:
        from app import mcp_tools

        sample = generate(1, seed + 5)
        direct, via = [], []
        for sc in sample:
            runtime.settings.mcp_tools = False
            direct.append(harness.run_case(sc))
            runtime.settings.mcp_tools = True
            via.append(harness.run_case(sc))
        runtime.settings.mcp_tools = False
        mcp_effect = {
            "ran": True, "cases": len(sample),
            "same_decision_score_and_ledger": sum(1 for a, b in zip(direct, via, strict=False) if (a["decision"], a["score"], a["tx_status"]) == (b["decision"], b["score"], b["tx_status"])),
            "same_tool_calls": sum(1 for a, b in zip(direct, via, strict=False) if sorted(e[1] for e in a["events"] if e[1]) == sorted(e[1] for e in b["events"] if e[1])),
            "latency_s": {"direct_p50": metrics.percentile([r["latency_s"] for r in direct], 50), "mcp_p50": metrics.percentile([r["latency_s"] for r in via], 50)},
            "mcp_calls": mcp_tools.bridge.calls, "fallbacks_to_direct": mcp_tools.bridge.fallbacks,
            "offered": {a: mcp_tools.offered(a) for a in ("telecom", "banking")},
            "money_tools_offered": sorted({t for a in ("telecom", "banking") for t in mcp_tools.offered(a)} & baseline.MONEY_TOOLS),
        }
    except ImportError:
        pass

    # ------------------------------------------------ hostile text in the payee field
    injected = []
    reference = {}
    rng = random.Random(seed + 1)
    for label, build in (("benign", routine_bill), ("attack", classic_takeover)):
        sc = build(rng)
        base = harness.run_case(sc)
        reference[label] = base["decision"]
        for payload in PAYLOADS:
            with harness.model_probe() as texts:
                r = harness.run_case(sc, payee_override=payload)
            calls, bad = harness.tool_audit(r["events"])
            injected.append({
                "scenario": label, "payload": payload[:60], "decision": r["decision"], "reference": base["decision"],
                "same_decision": r["decision"] == base["decision"], "status": r["status"], "outside_charter": bad,
                "raw_identifiers_to_model": harness.leaks_in(texts, [r["ctx"]]),
            })
    invariant = sum(1 for i in injected if i["same_decision"] and i["outside_charter"] == 0 and i["raw_identifiers_to_model"] == 0 and i["decision"] is not None)
    injection = {"cases": injected, "total": len(injected), "decision_unchanged_and_clean": invariant}

    return {
        "meta": {
            "generated_with": "python -m evaluation.run", "seed": seed, "per_archetype": per_archetype, "cases": len(scenarios),
            "model": "mock (deterministic)" if not baseline.model_available() else "a real model is configured",
            "python": platform.python_version(), "seconds": round(time.time() - started, 1),
            "data": "synthetic: archetypes in evaluation/archetypes.py. Labels come from how each scenario was built, not from the rules.",
        },
        "system": system, "agents": agents, "single_agent_baseline": single, "profile_effect": profile_effect, "calibration": calibration_result, "mcp": mcp_effect, "injection": injection,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-archetype", type=int, default=12)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="../docs/evaluation")
    args = parser.parse_args()
    results = run(args.per_archetype, args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    from evaluation.report import write_report

    write_report(results, out)
    print(f"Wrote {out}/results.json, results.md and charts. {results['meta']['cases']} cases in {results['meta']['seconds']}s.")


if __name__ == "__main__":
    main()
