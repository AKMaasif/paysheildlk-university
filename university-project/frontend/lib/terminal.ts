import type { LiveEvent } from "@/components/Shell";
import { colombo } from "@/lib/format";

export type TermKind = "tool" | "model" | "result" | "decision" | "success" | "error" | "handoff" | "note";
export type TermBucket = "tools" | "models" | "decisions" | "other";

export type TermLine = {
  key: string;
  event: LiveEvent;
  nodeId: string;
  kind: TermKind;
  bucket: TermBucket;
  time: string;
  tag: string;
  text: string;
};

const TAG: Record<string, string> = {
  supervisor: "supervisor",
  telecom: "telecom",
  banking: "banking",
  risk: "risk",
  policy: "policy",
  alert: "alert",
  verification: "verify",
  response: "response",
  case: "report",
  customer: "customer",
};

const TOOL_LABEL: Record<string, string> = {
  get_sim_swap_status: "camara.checkSimSwap",
  get_device_change: "camara.deviceChange",
  get_location_events: "camara.locationEvents",
  get_port_out_status: "camara.portOutStatus",
  get_recent_transactions: "bank.recentTransactions",
  compute_risk_score: "risk.score",
  hold_transaction: "bank.hold",
  block_transaction: "bank.block",
  release_transaction: "bank.release",
  request_sim_freeze: "telco.freezeSim",
  lock_account: "bank.lockAccount",
  send_push: "notify.push",
  send_email: "notify.email",
  send_sms: "notify.sms",
  ask_security_questions: "customer.securityQuestions",
  create_case: "case.open",
  generate_report: "case.fileReport",
};

const TOOL_NODE: Record<string, string> = {
  get_sim_swap_status: "sim_swap",
  get_device_change: "telecom",
  get_location_events: "telecom",
  get_port_out_status: "telecom",
  get_recent_transactions: "transactions",
  compute_risk_score: "risk",
  hold_transaction: "policy",
  block_transaction: "block",
  release_transaction: "release",
  request_sim_freeze: "freeze",
  lock_account: "lock",
  send_push: "push",
  send_email: "email",
  send_sms: "sms",
  ask_security_questions: "questions",
  create_case: "supervisor",
  generate_report: "report",
};

const AGENT_NODE: Record<string, string> = {
  supervisor: "supervisor",
  telecom: "telecom",
  banking: "banking",
  risk: "risk",
  policy: "policy",
  alert: "alert",
  verification: "verification",
  response: "response",
  case: "report",
  customer: "customer",
};

export function maskSecrets(text: string): string {
  return text
    .replace(/\+\d{7,}/g, (phone) => phone.slice(0, 5) + "*".repeat(Math.max(4, phone.length - 7)) + phone.slice(-2))
    .replace(/\b\d{8,}\b/g, (num) => num.slice(0, 2) + "*".repeat(num.length - 4) + num.slice(-2));
}

export function nodeFor(event: LiveEvent): string {
  if (/analyst|10 minutes/i.test(event.message || "")) return "escalate";
  if (event.agent === "customer" || event.handoff_to === "customer") return event.agent === "customer" ? "customer" : (event.tool_name ? TOOL_NODE[event.tool_name] || "customer" : "customer");
  if (event.tool_name === "compute_risk_score" && event.agent === "policy") return "policy";
  if (event.tool_name && TOOL_NODE[event.tool_name]) return TOOL_NODE[event.tool_name];
  return AGENT_NODE[event.agent] || event.agent;
}

function shortTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Colombo",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function stampFromFeed(raw: string): string {
  let iso = raw.trim().replace(/ UTC$/i, "Z").replace(" ", "T");
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(iso)) iso += "Z";
  return colombo(iso) || raw;
}

function formatArgs(args: Record<string, unknown> | null | undefined): string {
  if (!args) return "";
  return Object.entries(args).map(([key, value]) => {
    const name = key === "phoneNumber" ? "phone" : key;
    let shown = value == null ? "" : String(value);
    if (key === "phoneNumber" || /account/i.test(key)) shown = maskSecrets(shown);
    if (key === "maxAge") shown = `${shown}h`;
    return `${name}=${shown}`;
  }).join(", ");
}

function latency(event: LiveEvent): string {
  if (event.latency_ms == null) return "";
  if (event.latency_ms >= 1000) return `${(event.latency_ms / 1000).toFixed(1)}s`;
  return `${event.latency_ms} ms`;
}

function ruleSlug(text: string): string {
  const lower = text.toLowerCase();
  if (lower.includes("port-out") || lower.includes("port out")) return "port_out_critical";
  if (lower.includes("sim") && (lower.includes("held") || lower.includes("hold") || lower.includes("suspicious"))) return "sim_swap_recent_high_value";
  if (lower.includes("low-risk") || lower.includes("cleared") || lower.includes("let a")) return "routine_clear";
  return "desk_rule";
}

function policyLine(text: string): string {
  const decision = (text.match(/rule decision\s+(\w+)/i) || text.match(/\b(hold|block|release)\b/i) || ["", "hold"])[1];
  const score = (text.match(/score\s+(\d+)/i) || [])[1];
  return `policy: ${decision.toUpperCase()}${score ? ` score=${score}` : ""} rule=${ruleSlug(text)}`;
}

function shortResult(event: LiveEvent): string {
  const text = event.result_summary || "";
  const timing = latency(event);
  const tail = timing ? ` (${timing})` : "";
  if (/SIM was swapped/i.test(text)) {
    const when = text.match(/Latest change (.+?)\.?$/i);
    const latest = when ? stampFromFeed(when[1]) : "unknown";
    return `<- swapped=true latest=${latest}${tail}`;
  }
  if (/no sim swap/i.test(text)) return `<- swapped=false${tail}`;
  if (event.tool_name === "send_sms") return /failed/i.test(text) ? `<- sms=FAILED after retry, falling back to push and email${tail}` : `<- sms=log-only${tail}`;
  if (event.tool_name === "send_email") return /failed/i.test(text) ? `<- email=FAILED after retry, falling back to push and the in-app inbox${tail}` : `<- email=log-only${tail}`;
  if (event.tool_name === "send_push") return `<- push=in-app${tail}`;
  if (event.tool_name === "hold_transaction") return `<- held${tail}`;
  if (event.tool_name === "block_transaction") return `<- blocked${tail}`;
  if (event.tool_name === "release_transaction") return `<- released${tail}`;
  if (event.tool_name === "request_sim_freeze") return `<- sim frozen${tail}`;
  if (event.tool_name === "lock_account") return `<- account locked${tail}`;
  if (event.tool_name === "generate_report") return `<- report filed${tail}`;
  if (event.tool_name === "compute_risk_score" && event.agent === "policy") return policyLine(text);
  const score = text.match(/Score (\d+) from (\d+)/i);
  if (score) return `<- score=${score[1]} factors=${score[2]}${tail}`;
  const count = text.match(/^(\d+)\s/);
  if (count) return `<- ${count[1]} events${tail}`;
  if (!text) return timing ? `<- ${timing}` : "";
  const brief = text.replace(/\s+/g, " ").slice(0, 90);
  return `<- ${brief}${tail}`;
}

function modelLine(event: LiveEvent): string {
  if (event.fell_back) return `fallback -> ${event.provider || "meta"} ${event.model || ""}`.trim();
  const name = event.model || event.provider || "model";
  const took = latency(event);
  return `${name} ok${took ? ` ${took}` : ""}`;
}

function successTool(name: string | null | undefined): boolean {
  return ["block_transaction", "release_transaction", "request_sim_freeze", "lock_account", "generate_report"].includes(name || "");
}

export function terminalLines(event: LiveEvent, timers?: { realert: number; escalate: number }): TermLine[] {
  const time = shortTime(event.created_at);
  const tag = TAG[event.agent] || event.agent;
  const nodeId = nodeFor(event);
  const lines: TermLine[] = [];
  const push = (suffix: string, kind: TermKind, bucket: TermBucket, text: string) => {
    lines.push({ key: `${event.id}${suffix}`, event, nodeId, kind, bucket, time, tag, text: maskSecrets(text) });
  };

  if (event.state === "error") {
    push("", "error", "other", event.message || event.result_summary || "The step failed.");
    return lines;
  }

  if (event.tool_name) {
    const label = TOOL_LABEL[event.tool_name] || event.tool_name;
    push(":call", "tool", "tools", `${tag}.call ${label}(${formatArgs(event.tool_args)})`);
    const result = shortResult(event);
    if (result) {
      const decision = event.tool_name === "compute_risk_score" && event.agent === "policy";
      const ok = successTool(event.tool_name);
      const failed = /=FAILED/.test(result);
      push(":res", failed ? "error" : decision ? "decision" : ok ? "success" : "result", decision || ok ? "decisions" : "tools", result);
    }
  }

  if (event.handoff_from && event.handoff_to) {
    const dest = TAG[event.handoff_to] || event.handoff_to;
    push(":hand", "handoff", "decisions", `${event.handoff_from} -> ${dest} handoff`);
  }

  if (event.agent === "customer") {
    const answer = /no/i.test(event.message || "") ? "NO" : "YES";
    push(":answer", answer === "YES" ? "success" : "decision", "decisions", `customer: ${answer}`);
    return lines;
  }

  if (event.state === "waiting_for_customer") {
    const re = Math.round((timers?.realert || 180) / 60);
    const es = Math.round((timers?.escalate || 600) / 60);
    push(":wait", "decision", "decisions", `waiting on customer · re-alert ${re} min · escalate ${es} min`);
    return lines;
  }

  if (event.agent === "policy" && event.state === "done" && !event.tool_name) {
    push(":policy", "decision", "decisions", policyLine(event.result_summary || event.message || ""));
  }

  if ((event.provider === "rules" || event.provider === "human") && event.agent !== "policy" && !event.tool_name && event.state !== "thinking" && event.message) {
    // The rules and the analyst explain themselves in words. Show that, not "deterministic ok".
    push(":rule", "decision", "decisions", event.message);
  } else if ((event.provider || event.model) && !event.tool_name && event.state !== "thinking") {
    push(":model", "model", "models", modelLine(event));
  } else if (event.state === "thinking" && event.message) {
    push(":think", "note", "other", event.message);
  }

  return lines;
}
