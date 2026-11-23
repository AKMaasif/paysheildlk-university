"""The case graph: which agent step runs after which. The steps themselves are in app/agents/nodes, and what they share is in app/agents/base."""
import logging

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app import ops
from app.agents.base import (  # noqa: F401  (re-exported for tests)
    CaseState,
    _receive,
    _send,
    _tool,
    pace,
)
from app.agents.nodes.alert import alert
from app.agents.nodes.banking import banking
from app.agents.nodes.customer import route_customer, wait_customer
from app.agents.nodes.policy import policy, route_policy
from app.agents.nodes.release import release
from app.agents.nodes.report import report
from app.agents.nodes.response import response
from app.agents.nodes.risk import risk
from app.agents.nodes.supervisor import escalate, fanout, supervisor
from app.agents.nodes.telecom import telecom
from app.agents.nodes.verification import (
    route_verification,
    verification_prep,
    verify_wait,
)
from app.events import emit

log = logging.getLogger("payshield.graph")


def _guard(agent: str, fn):
    def inner(state: CaseState):
        try:
            from app import faults

            if faults.take_agent_failure(agent):
                raise faults.SimulatedFailure(f"The {agent} agent stopped responding (drill).")
            return fn(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            case_id = state.get("case_id", "")
            log.exception("%s failed", agent)
            ops.count("agent_errors", agent=agent)
            try:
                if case_id:
                    # The runner's fail-safe decides what the case becomes. The rule explanation stays as it was.
                    emit(
                        case_id,
                        agent=agent,
                        state="error",
                        message=str(exc)[:300],
                        result_summary=str(exc)[:300],
                    )
            except Exception:
                log.exception("could not record the failure")
            raise

    return inner


def build_graph(checkpointer):
    graph = StateGraph(CaseState)
    graph.add_node("supervisor", _guard("supervisor", supervisor))
    graph.add_node("telecom", _guard("telecom", telecom))
    graph.add_node("banking", _guard("banking", banking))
    graph.add_node("risk", _guard("risk", risk))
    graph.add_node("policy", _guard("policy", policy))
    graph.add_node("alert", _guard("alert", alert))
    graph.add_node("wait_customer", _guard("alert", wait_customer))
    graph.add_node("verification_prep", _guard("verification", verification_prep))
    graph.add_node("verify_wait", _guard("verification", verify_wait))
    graph.add_node("release", _guard("verification", release))
    graph.add_node("response", _guard("response", response))
    graph.add_node("escalate", _guard("supervisor", escalate))
    graph.add_node("report", _guard("case", report))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", fanout, ["telecom", "banking"])
    graph.add_edge("telecom", "risk")
    graph.add_edge("banking", "risk")
    graph.add_edge("risk", "policy")
    graph.add_conditional_edges("policy", route_policy, ["alert", "response", "release"])
    graph.add_edge("alert", "wait_customer")
    graph.add_conditional_edges(
        "wait_customer",
        route_customer,
        ["verification_prep", "response", "escalate"],
    )
    graph.add_edge("verification_prep", "verify_wait")
    graph.add_conditional_edges("verify_wait", route_verification, ["release", "response", "escalate", "verify_wait"])
    graph.add_edge("release", "report")
    graph.add_edge("response", "report")
    graph.add_edge("escalate", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}, "recursion_limit": 80}


def invoke_initial(case_id: str, state: dict):
    from app.runtime import runtime

    return runtime.graph.invoke(state, thread_config(case_id))


def invoke_resume(case_id: str, value: dict):
    from app.runtime import runtime

    return runtime.graph.invoke(Command(resume=value), thread_config(case_id))
