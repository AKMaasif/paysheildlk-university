"""python -m qa.coverage_by_agent coverage.json

Reads a coverage.py JSON report and prints line and branch coverage per module and per agent, using the
agent-to-code map from docs/TEST_PLAN.md. Whole-file coverage is a rough proxy for agents that share a file
(the graph file holds every agent's node)."""
import json
import sys

AGENTS = {
    "Supervisor": ["app/agents/runner.py", "app/protocol.py", "app/events.py"],
    "Telecom": ["app/facts.py", "app/mcp_tools.py", "app/investigator.py"],
    "Banking": ["app/profile.py"],
    "Risk": ["app/risk.py"],
    "Policy": ["app/emergency.py", "app/calibration.py"],
    "Alert": ["app/notify.py"],
    "Verification": ["app/face.py", "app/liveness.py", "app/knowledge.py", "app/answers.py"],
    "Response": ["app/ledger.py", "app/manual.py"],
    "Report": ["app/reports.py", "app/report_pdf.py", "app/critic.py"],
    "Analytics": ["app/analytics.py", "app/simbook.py", "app/geo.py"],
}
NODES = [f"app/agents/nodes/{name}.py" for name in ("supervisor", "telecom", "banking", "risk", "policy", "alert", "customer", "verification", "release", "response", "report")]
ROUTES = [f"app/routes/{name}.py" for name in ("common", "auth", "console", "customer", "admin", "demo", "stream", "telco", "health")]
SHARED = ["app/agents/graph.py", "app/agents/base.py", *NODES, "app/agents/tools.py", "app/charters.py", "app/privacy.py"]
SYSTEM = ["app/api.py", *ROUTES, "app/desk.py", "app/customer.py", "app/simulate.py", "app/security.py", "app/ratelimit.py", "app/crypto.py",
          "app/config.py", "app/runtime.py", "app/db.py", "app/seed.py", "app/demo.py", "app/faults.py", "app/llm.py", "app/util.py", "app/main.py", "app/models.py"]


def pct(f):
    s = f["summary"]
    lines = s["covered_lines"] / s["num_statements"] * 100 if s["num_statements"] else 100.0
    branches = s.get("covered_branches", 0) / s["num_branches"] * 100 if s.get("num_branches") else 100.0
    return lines, branches, s["num_statements"], s["covered_lines"], s.get("num_branches", 0), s.get("covered_branches", 0)


def main(path):
    data = json.load(open(path))
    files = {k.replace("\\", "/"): v for k, v in data["files"].items()}
    rows = {}
    for name in [f for fs in AGENTS.values() for f in fs] + SHARED + SYSTEM:
        key = next((k for k in files if k.endswith(name)), None)
        if key:
            rows[name] = pct(files[key])
    print(f"{'module':32s} {'lines':>7s} {'branch':>7s}  statements")
    for name, (l, b, n, c, nb, cb) in sorted(rows.items(), key=lambda kv: kv[1][0]):
        print(f"{name:32s} {l:6.1f}% {b:6.1f}%  {c}/{n}")
    print("\nper agent (own modules; the shared graph is listed above)")
    for agent, mods in AGENTS.items():
        n = sum(rows[m][2] for m in mods if m in rows)
        c = sum(rows[m][3] for m in mods if m in rows)
        nb = sum(rows[m][4] for m in mods if m in rows)
        cb = sum(rows[m][5] for m in mods if m in rows)
        print(f"{agent:14s} lines {c / n * 100 if n else 100:5.1f}%  branches {cb / nb * 100 if nb else 100:5.1f}%  ({c}/{n} statements)")
    t = data["totals"]
    print(f"\nTOTAL lines {t['percent_covered']:.1f}%  statements {t['covered_lines']}/{t['num_statements']}  branches {t.get('covered_branches', 0)}/{t.get('num_branches', 0)}")


if __name__ == "__main__":
    main(sys.argv[1])
