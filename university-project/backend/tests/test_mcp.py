"""The read-only tools offered through the Model Context Protocol."""
import asyncio
import dataclasses
import json
import random

import pytest

pytest.importorskip("mcp")

from mcp.client.client import Client  # noqa: E402

from app import mcp_tools  # noqa: E402
from app.agents import tools  # noqa: E402
from app.charters import CHARTERS  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import Transaction  # noqa: E402
from app.runtime import runtime  # noqa: E402
from evaluation import harness  # noqa: E402
from evaluation.archetypes import classic_takeover, routine_bill  # noqa: E402

MONEY = {"hold_transaction", "release_transaction", "block_transaction", "lock_account", "request_sim_freeze"}


def _listed(agent):
    async def go():
        async with Client(mcp_tools.build_server(agent)) as client:
            return sorted(t.name for t in (await client.list_tools()).tools)

    return asyncio.run(go())


@pytest.fixture
def fresh_bridge(monkeypatch):
    bridge = mcp_tools.Bridge()
    monkeypatch.setattr(mcp_tools, "bridge", bridge)
    yield bridge
    bridge.close()


def test_each_server_offers_only_its_charter_and_only_reads():
    for agent in ("telecom", "banking"):
        assert _listed(agent) == mcp_tools.offered(agent) == sorted(set(CHARTERS[agent].tools) & set(mcp_tools.READ_TOOLS))
        assert not set(_listed(agent)) & MONEY
    # Agents with no read tools get an empty server, and are not even offered a name.
    for agent in ("policy", "alert", "response", "verification", "risk"):
        assert mcp_tools.offered(agent) == [], agent


def test_no_money_tool_is_registered_anywhere():
    assert not set(mcp_tools.READ_TOOLS) & MONEY
    assert set(mcp_tools._IMPL) == set(mcp_tools.READ_TOOLS)


def test_a_lookup_through_mcp_returns_what_the_direct_call_returns(boot, fresh_bridge):
    ctx = harness.seed_scenario(classic_takeover(random.Random(1)))
    direct = tools.get_recent_transactions(ctx["customer_id"])
    via = fresh_bridge.call("banking", "get_recent_transactions", {"customer_id": ctx["customer_id"]})
    assert json.loads(json.dumps(via, default=str)) == json.loads(json.dumps(direct, default=str))
    swap = fresh_bridge.call("telecom", "get_sim_swap_status", {"phoneNumber": ctx["phone"], "maxAge": 240})
    assert swap["checkSimSwap"]["swapped"] is True


def test_a_tool_outside_the_agents_list_cannot_be_called(boot, fresh_bridge):
    ctx = harness.seed_scenario(classic_takeover(random.Random(1)))
    # A banking tool on the telecom server, and a money tool on any server.
    for agent, tool, args in (("telecom", "get_recent_transactions", {"customer_id": ctx["customer_id"]}), ("telecom", "release_transaction", {}), ("banking", "block_transaction", {})):
        with pytest.raises(Exception):
            fresh_bridge.call(agent, tool, args)
    with session_scope() as session:
        assert session.get(Transaction, ctx["transaction_id"]).status == "pending"


def test_the_charter_is_checked_again_when_the_call_arrives(boot, monkeypatch):
    ctx = harness.seed_scenario(classic_takeover(random.Random(1)))
    server_bridge = mcp_tools.Bridge()
    try:
        # Build the server while the tool is allowed, then withdraw it from the charter before the call.
        server = mcp_tools.build_server("telecom")
        trimmed = dataclasses.replace(CHARTERS["telecom"], tools=tuple(t for t in CHARTERS["telecom"].tools if t != "get_port_out_status"))
        monkeypatch.setitem(CHARTERS, "telecom", trimmed)

        async def go():
            async with Client(server) as client:
                return await client.call_tool("get_port_out_status", {"phoneNumber": ctx["phone"]})

        result = asyncio.run(go())
        assert result.is_error
    finally:
        server_bridge.close()


def _decide(scenario, mcp_on, monkeypatch):
    monkeypatch.setattr(runtime.settings, "mcp_tools", mcp_on)
    return harness.run_case(scenario)


def test_the_graph_gives_the_same_outcome_with_mcp_on_or_off(boot, fresh_bridge, monkeypatch):
    for build, seed in ((classic_takeover, 3), (routine_bill, 4)):
        sc = build(random.Random(seed))
        off = _decide(sc, False, monkeypatch)
        before = fresh_bridge.calls
        on = _decide(sc, True, monkeypatch)
        assert fresh_bridge.calls > before
        assert (on["decision"], on["score"], on["tx_status"]) == (off["decision"], off["score"], off["tx_status"])
        names = lambda run: sorted(e[1] for e in run["events"] if e[1] and e[1] != "verify_handoff")
        assert names(on) == names(off)
        assert harness.tool_audit(on["events"])[1] == 0


def test_if_mcp_itself_fails_the_case_still_finishes_and_the_fallback_is_counted(boot, fresh_bridge, monkeypatch):
    monkeypatch.setattr(runtime.settings, "mcp_tools", True)
    monkeypatch.setattr(fresh_bridge, "call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transport down")))
    run = harness.run_case(classic_takeover(random.Random(3)))
    assert run["decision"] == "hold" and fresh_bridge.fallbacks > 0
