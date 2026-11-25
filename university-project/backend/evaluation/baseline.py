"""The single-agent baseline: one component that reads every feed, holds every tool and decides alone.

Two honest limits on what this can show:
  * With no model key the "model" baseline cannot run. The mock model has no judgement, so a number
    from it would be invented. It is reported as not run.
  * The rules baseline uses the same rule table as the multi-agent system, so it should match on
    accuracy. What differs is speed, what each part can reach, and how much is recorded.
"""

import time

from evaluation.archetypes import Scenario

MONEY_TOOLS = {"hold_transaction", "block_transaction", "release_transaction", "lock_account", "request_sim_freeze"}
# Agents whose input includes text typed by whoever sent the payment (the payee name).
TEXT_EXPOSED = ["banking", "alert", "case"]

ALL_TOOLS = [
    "create_case", "get_sim_swap_status", "get_device_change", "get_location_events", "get_port_out_status",
    "get_recent_transactions", "compute_risk_score", "hold_transaction", "release_transaction", "block_transaction",
    "request_sim_freeze", "lock_account", "send_push", "send_email", "send_sms", "liveness_check", "ask_security_questions",
    "generate_report", "deliver_report",
]


def reach() -> dict:
    """What each agent can reach, taken straight from the charters, against a single agent that holds everything."""
    from app.charters import CHARTERS

    per_agent = {name: sorted(set(c.tools) & MONEY_TOOLS) for name, c in CHARTERS.items()}
    exposed = {name: per_agent[name] for name in TEXT_EXPOSED if name in per_agent}
    return {
        "per_agent": per_agent,
        "text_exposed_agents": exposed,
        "multi_agent_max_for_text_exposed": max((len(v) for v in exposed.values()), default=0),
        "multi_agent_max_any_agent": max(len(v) for v in per_agent.values()),
        "single_agent": sorted(MONEY_TOOLS),
        "single_agent_count": len(MONEY_TOOLS),
    }


def rules_single(sc: Scenario, use_profile: bool | None = None) -> dict:
    """One function, no handoffs, no events, no signatures: collect, decide, act."""
    from app.facts import collect_signals
    from app.ledger import block_transaction, hold_transaction, release_transaction
    from app.risk import decide_policy
    from evaluation.harness import open_case, seed_scenario

    ctx = seed_scenario(sc)
    case_id = open_case(ctx, sc.amount)
    started = time.perf_counter()
    signals, _context = collect_signals(ctx["customer_id"], ctx["transaction_id"], use_profile=use_profile)
    result = decide_policy(signals)
    if result.decision == "block":
        block_transaction(ctx["transaction_id"], case_id, "baseline")
    elif result.decision == "hold":
        hold_transaction(ctx["transaction_id"], case_id)
    else:
        release_transaction(ctx["transaction_id"], case_id, "baseline")
    return {"decision": result.decision, "latency_s": time.perf_counter() - started}


def model_available() -> bool:
    from app.runtime import runtime

    s = runtime.settings
    return bool(s.meta_api_key or s.xai_api_key)


def model_single(sc: Scenario) -> dict:
    """One model call that sees all the facts and chooses. Unparseable answers fail safe to hold."""
    from app.facts import collect_signals
    from app.runtime import runtime
    from evaluation.harness import seed_scenario

    ctx = seed_scenario(sc)
    signals, _context = collect_signals(ctx["customer_id"], ctx["transaction_id"])
    facts = "\n".join(f"{k}: {v}" for k, v in signals.__dict__.items())
    started = time.perf_counter()
    reply = runtime.llm.complete(
        system="You decide what a bank does with a payment. Answer with exactly one word: release, hold, or block.",
        user=facts, purpose="baseline",
    )
    word = next((w for w in ("block", "hold", "release") if w in reply.text.lower()), "hold")
    return {"decision": word, "latency_s": time.perf_counter() - started, "provider": reply.provider}
