"""The read-only tools of the Telecom and Banking agents, offered through the Model Context Protocol (MCP).

Why: MCP is the open standard for giving an agent tools. With it, the same lookups can be reached by the
graph's own agents, by an analyst's assistant, or by any MCP client, and the list of tools each caller
sees is the list in its charter.

How it is kept safe:
  * Only read tools are ever offered. Money-moving tools (hold, release, block, lock, freeze) are not
    registered anywhere in this module, whatever a charter says.
  * There is one server per agent. It registers only the tools in that agent's charter, and every call
    is checked against the charter again when it arrives.
  * The decision does not depend on this layer. The risk score and the rules read the ledger and the
    operator feed directly, so a failed or hostile tool call here cannot change a hold, block or release.

Two ways to use it:
  * Inside the graph (MCP_TOOLS=on): each agent's lookups go through an MCP client talking to its own
    server over an in-memory connection. Nothing leaves the process.
  * From outside: `python -m app.mcp_tools serve telecom` runs that agent's server over stdio.
"""

import asyncio
import contextlib
import functools
import json
import logging
import threading

from app.charters import CHARTERS, enforce

log = logging.getLogger("payshield.mcp")

READ_TOOLS = {
    "get_sim_swap_status": "SIM swap check and date for a phone number (CAMARA SIM Swap shape)",
    "get_device_change": "Recent handset (IMEI) changes on a phone number",
    "get_location_events": "Recent serving-location changes on a phone number",
    "get_port_out_status": "Open or recent port-out requests on a phone number",
    "get_sim_history": "Every SIM event on a phone number in the last 30 days",
    "get_recent_transactions": "A customer's recent ledger entries",
    "get_customer_profile": "What is usual for a customer: payees, amounts and hours",
    "get_payee_history": "Earlier payments from a customer to one payee",
    "get_credential_events": "A customer's sign-ins and password resets in the last 48 hours",
}


class McpToolError(RuntimeError):
    pass


def _tools():
    from app.agents import tools

    return tools


# Typed functions: MCP builds each tool's input schema from these signatures.
def get_sim_swap_status(phoneNumber: str, maxAge: int = 240) -> dict:
    return _tools().get_sim_swap_status(phoneNumber, maxAge)


def get_device_change(phoneNumber: str) -> dict:
    return _tools().get_device_change(phoneNumber)


def get_location_events(phoneNumber: str) -> dict:
    return _tools().get_location_events(phoneNumber)


def get_port_out_status(phoneNumber: str) -> dict:
    return _tools().get_port_out_status(phoneNumber)


def get_sim_history(phoneNumber: str) -> dict:
    return _tools().get_sim_history(phoneNumber)


def get_recent_transactions(customer_id: str) -> dict:
    return _tools().get_recent_transactions(customer_id)


def get_customer_profile(customer_id: str, payee: str, amount_lkr: int) -> dict:
    return _tools().get_customer_profile(customer_id, payee, amount_lkr)


def get_payee_history(customer_id: str, payee: str) -> dict:
    return _tools().get_payee_history(customer_id, payee)


def get_credential_events(customer_id: str) -> dict:
    return _tools().get_credential_events(customer_id)


_IMPL = {fn.__name__: fn for fn in (
    get_sim_swap_status, get_device_change, get_location_events, get_port_out_status, get_sim_history,
    get_recent_transactions, get_customer_profile, get_payee_history, get_credential_events,
)}
if set(_IMPL) != set(READ_TOOLS):
    raise RuntimeError("the MCP tool list and the read tools have drifted apart")


def offered(agent: str) -> list[str]:
    """The tools this agent's server registers: its charter, intersected with the read-only list."""
    charter = CHARTERS.get(agent)
    return sorted(set(charter.tools) & set(READ_TOOLS)) if charter else []


def build_server(agent: str):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        f"payshield-{agent}",
        instructions=f"Read-only PayShield lookups for the {agent} agent. These tools never move money.",
    )
    for name in offered(agent):
        fn = _IMPL[name]

        def make(tool_name, impl):
            @functools.wraps(impl)
            def checked(*args, **kwargs):
                enforce(agent, tool_name)  # checked again at the moment of the call
                return impl(*args, **kwargs)

            return checked

        server.add_tool(make(name, fn), name=name, description=READ_TOOLS[name])
    return server


class Bridge:
    """Runs MCP clients on one background event loop so the graph's worker threads can call them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._clients: dict = {}
        self.calls = 0
        self.fallbacks = 0

    def _ensure_loop(self):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._loop.run_forever, name="payshield-mcp", daemon=True)
                self._thread.start()
        return self._loop

    async def _client(self, agent: str):
        entry = self._clients.get(agent)
        if entry is None:
            from mcp.client.client import Client

            stack = contextlib.AsyncExitStack()
            client = await stack.enter_async_context(Client(build_server(agent)))
            entry = self._clients[agent] = (client, stack)
        return entry[0]

    async def _call(self, agent: str, tool: str, args: dict) -> dict:
        client = await self._client(agent)
        result = await client.call_tool(tool, args)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise McpToolError(text or "tool call failed")
        data = result.structured_content
        if data is None:
            data = json.loads(result.content[0].text)
        if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]
        return data

    def call(self, agent: str, tool: str, args: dict) -> dict:
        loop = self._ensure_loop()
        self.calls += 1
        future = asyncio.run_coroutine_threadsafe(self._call(agent, tool, args), loop)
        return future.result(timeout=30)

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return

        async def shut():
            for _client, stack in list(self._clients.values()):
                await stack.aclose()
            self._clients.clear()

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(shut(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        self._loop = None
        self._thread = None


bridge = Bridge()


def call_tool(agent: str, tool: str, args: dict) -> dict:
    """One lookup through MCP. If the MCP layer itself fails, the lookup falls back to the direct call and
    the fallback is counted. A tool refused by the charter is not retried: that is the check working."""
    from app.charters import CharterViolation

    try:
        return bridge.call(agent, tool, args)
    except (CharterViolation, McpToolError) as exc:
        raise exc
    except Exception as exc:
        bridge.fallbacks += 1
        log.warning("MCP call failed (%s). Using the direct call for %s.", exc, tool)
        return _IMPL[tool](**args)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Serve one agent's read-only PayShield tools over MCP (stdio)")
    parser.add_argument("command", choices=["serve", "list"])
    parser.add_argument("agent", choices=sorted(a for a in CHARTERS if offered(a)))
    args = parser.parse_args()
    if args.command == "list":
        print("\n".join(offered(args.agent)))
        return
    from app.runtime import init_runtime

    init_runtime()
    build_server(args.agent).run(transport="stdio")


if __name__ == "__main__":
    main()
