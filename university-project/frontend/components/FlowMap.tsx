"use client";

import { useEffect, useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { LiveEvent } from "@/components/Shell";

const X = (column: number) => column * 250;

type Kind = "tool" | "agent" | "human";
type Branch = "common" | "release" | "block" | "escalate";
type Chip = "Waiting" | "Running" | "Done" | "Unavailable" | "Skipped";

type Spec = {
  id: string;
  kind: Kind;
  name: string;
  kicker: string;
  x: number;
  y: number;
  branch: Branch;
  agent?: string;
  tool?: string;
};

const SPECS: Spec[] = [
  { id: "supervisor", kind: "agent", name: "Supervisor", kicker: "AI AGENT", x: X(0), y: 46, branch: "common", agent: "supervisor" },
  { id: "telecom", kind: "agent", name: "Telecom", kicker: "AI AGENT", x: X(1), y: 0, branch: "common", agent: "telecom" },
  { id: "banking", kind: "agent", name: "Banking", kicker: "AI AGENT", x: X(1), y: 108, branch: "common", agent: "banking" },
  { id: "sim_swap", kind: "tool", name: "SIM swap check", kicker: "TOOL", x: X(2), y: 0, branch: "common", agent: "telecom", tool: "get_sim_swap_status" },
  { id: "transactions", kind: "tool", name: "Transaction lookup", kicker: "TOOL", x: X(2), y: 108, branch: "common", agent: "banking", tool: "get_recent_transactions" },
  { id: "risk", kind: "agent", name: "Risk", kicker: "AI AGENT", x: X(3), y: 46, branch: "common", agent: "risk" },
  { id: "policy", kind: "tool", name: "Policy rules", kicker: "TOOL", x: X(3), y: 230, branch: "common", agent: "policy" },
  { id: "alert", kind: "agent", name: "Alert", kicker: "AI AGENT", x: X(2), y: 230, branch: "common", agent: "alert" },
  { id: "push", kind: "tool", name: "Push", kicker: "TOOL", x: X(1), y: 230, branch: "common", agent: "alert", tool: "send_push" },
  { id: "email", kind: "tool", name: "Email", kicker: "TOOL", x: X(0), y: 230, branch: "common", agent: "alert", tool: "send_email" },
  { id: "sms", kind: "tool", name: "SMS", kicker: "TOOL", x: X(0), y: 338, branch: "common", agent: "alert", tool: "send_sms" },
  { id: "customer", kind: "human", name: "Yes or no", kicker: "CUSTOMER", x: X(1), y: 338, branch: "common" },
  { id: "verification", kind: "agent", name: "Verification", kicker: "AI AGENT", x: X(0), y: 460, branch: "release", agent: "verification" },
  { id: "questions", kind: "tool", name: "Security questions", kicker: "TOOL", x: X(1), y: 460, branch: "release", agent: "verification", tool: "ask_security_questions" },
  { id: "release", kind: "tool", name: "Release transfer", kicker: "TOOL", x: X(2), y: 460, branch: "release", tool: "release_transaction" },
  { id: "escalate", kind: "human", name: "Analyst escalation", kicker: "HUMAN REVIEW", x: X(2), y: 338, branch: "escalate", agent: "supervisor" },
  { id: "response", kind: "agent", name: "Response", kicker: "AI AGENT", x: X(0), y: 568, branch: "block", agent: "response" },
  { id: "block", kind: "tool", name: "Block transfer", kicker: "TOOL", x: X(1), y: 568, branch: "block", agent: "response", tool: "block_transaction" },
  { id: "freeze", kind: "tool", name: "Freeze SIM", kicker: "TOOL", x: X(2), y: 568, branch: "block", agent: "response", tool: "request_sim_freeze" },
  { id: "lock", kind: "tool", name: "Lock account", kicker: "TOOL", x: X(3), y: 568, branch: "block", agent: "response", tool: "lock_account" },
  { id: "report", kind: "agent", name: "Case report", kicker: "AI AGENT", x: X(3), y: 460, branch: "common", agent: "case" },
];

const LINKS: Array<{ source: string; target: string; from: string; to: string }> = [
  { source: "supervisor", target: "telecom", from: "out", to: "in" },
  { source: "supervisor", target: "banking", from: "out", to: "in" },
  { source: "telecom", target: "sim_swap", from: "out", to: "in" },
  { source: "banking", target: "transactions", from: "out", to: "in" },
  { source: "sim_swap", target: "risk", from: "out", to: "in" },
  { source: "transactions", target: "risk", from: "out", to: "in" },
  { source: "risk", target: "policy", from: "down", to: "up" },
  { source: "policy", target: "alert", from: "in", to: "out" },
  { source: "alert", target: "push", from: "in", to: "out" },
  { source: "push", target: "email", from: "in", to: "out" },
  { source: "email", target: "sms", from: "down", to: "up" },
  { source: "sms", target: "customer", from: "out", to: "in" },
  { source: "customer", target: "verification", from: "down", to: "out" },
  { source: "customer", target: "escalate", from: "out", to: "in" },
  { source: "customer", target: "response", from: "down", to: "out" },
  { source: "verification", target: "questions", from: "out", to: "in" },
  { source: "questions", target: "release", from: "out", to: "in" },
  { source: "release", target: "report", from: "out", to: "in" },
  { source: "escalate", target: "report", from: "out", to: "up" },
  { source: "response", target: "block", from: "out", to: "in" },
  { source: "block", target: "freeze", from: "out", to: "in" },
  { source: "freeze", target: "lock", from: "out", to: "in" },
  { source: "lock", target: "report", from: "up-out", to: "down-in" },
];

const LATER: Record<string, string[]> = {
  trigger: ["telecom", "banking", "risk", "policy", "alert", "customer", "verification", "response", "case"],
  telecom: ["risk", "policy", "alert", "customer", "verification", "response", "case"],
  banking: ["risk", "policy", "alert", "customer", "verification", "response", "case"],
  risk: ["policy", "alert", "customer", "verification", "response", "case"],
  policy: ["alert", "customer", "verification", "response", "case"],
  alert: ["customer", "verification", "response", "case"],
  customer: ["verification", "response", "case"],
  verification: ["case"],
  response: ["case"],
};

function eventState(id: string, events: LiveEvent[]): string {
  if (id === "trigger") return events.length ? "done" : "idle";
  if (id === "customer") {
    const waiting = [...events].reverse().find((event) => event.state === "waiting_for_customer");
    const answered = events.some((event) => event.agent === "customer");
    if (answered) return "done";
    if (waiting) return "waiting_for_customer";
    return "idle";
  }
  const latest = [...events].reverse().find((event) => event.agent === id);
  if (!latest) return "idle";
  if (latest.state === "waiting_for_customer" && events.some((event) => event.agent === "customer")) return "done";
  return latest.state;
}

function laterStarted(id: string, events: LiveEvent[]): boolean {
  const later = new Set(LATER[id] || []);
  return events.some((event) => later.has(event.agent) || (later.has("customer") && event.handoff_to === "customer"));
}

export function stageState(id: string, events: LiveEvent[], status?: string | null): string {
  const waitingOn = status === "waiting_customer" ? "customer" : status === "waiting_verification" ? "verification" : null;
  if (waitingOn === id) return "waiting_for_customer";
  const raw = eventState(id, events);
  const settled = status === "waiting_customer"
    || status === "waiting_verification"
    || status === "escalating"
    || status === "escalated"
    || (status || "").startsWith("closed");
  if (settled && id !== waitingOn && (raw === "calling_tool" || raw === "thinking" || raw === "waiting_for_customer")) return "done";
  if ((raw === "calling_tool" || raw === "thinking") && laterStarted(id, events)) return "done";
  return raw;
}

function owned(spec: Spec, events: LiveEvent[]): LiveEvent[] {
  if (spec.id === "policy") return events.filter((event) => event.agent === "policy" && event.tool_name !== "release_transaction");
  if (spec.id === "escalate") {
    return events.filter((event) => event.agent === "supervisor" && /analyst|10 minutes/i.test(event.message || ""));
  }
  if (spec.id === "customer") return events.filter((event) => event.agent === "customer" || event.handoff_to === "customer");
  if (spec.tool) return events.filter((event) => event.tool_name === spec.tool);
  if (spec.agent) return events.filter((event) => event.agent === spec.agent && !event.tool_name);
  return [];
}

function finishedAfter(event: LiveEvent, events: LiveEvent[]): boolean {
  const index = events.findIndex((item) => item.id === event.id);
  return index >= 0 && index < events.length - 1;
}

function blockStarted(events: LiveEvent[], status?: string | null): boolean {
  return status === "closed_blocked" || events.some((event) => event.tool_name === "block_transaction" || event.agent === "response");
}

function releaseStarted(events: LiveEvent[], status?: string | null): boolean {
  return status === "closed_released" || status === "closed_cleared" || events.some((event) => event.tool_name === "release_transaction");
}

function chipFor(spec: Spec, events: LiveEvent[], status?: string | null): Chip {
  if (spec.id === "customer") {
    if (status === "waiting_customer") return "Waiting";
    if (events.some((event) => event.agent === "customer")) return "Done";
    if (blockStarted(events, status) || releaseStarted(events, status) || status === "escalated") return "Skipped";
    return "Unavailable";
  }
  if (spec.id === "escalate") {
    if (status === "escalating") return "Running";
    if (status === "escalated") return "Done";
    if (blockStarted(events, status) || releaseStarted(events, status)) return "Skipped";
    return "Unavailable";
  }
  const mine = owned(spec, events);
  if (spec.id === "verification" && status === "waiting_verification" && mine.some((event) => event.state === "waiting_for_customer")) {
    return "Waiting";
  }
  if (mine.length) {
    const last = mine[mine.length - 1];
    const busy = last.state === "thinking" || last.state === "calling_tool";
    if (busy && !finishedAfter(last, events)) return "Running";
    return "Done";
  }
  if (spec.id === "report") return "Unavailable";
  if (spec.branch === "release") {
    if (blockStarted(events, status) || status === "escalated" || status === "escalating") return "Skipped";
    return "Unavailable";
  }
  if (spec.branch === "block") {
    if (releaseStarted(events, status) || status === "escalated" || status === "escalating") return "Skipped";
    return "Unavailable";
  }
  const passed = status === "waiting_customer"
    || status === "waiting_verification"
    || status === "escalating"
    || status === "escalated"
    || (status || "").startsWith("closed");
  return passed ? "Skipped" : "Unavailable";
}

function pickEvent(spec: Spec, events: LiveEvent[]): LiveEvent | undefined {
  const mine = owned(spec, events);
  return mine[mine.length - 1];
}

function TraceNode({ data }: { data: { name: string; kicker: string; kind: Kind; chip: Chip; active: boolean; focused: boolean } }) {
  const quiet = data.chip === "Skipped" || data.chip === "Unavailable";
  return (
    <div className={`trace-node kind-${data.kind} ${quiet ? "quiet" : ""} ${data.active ? "active" : ""} ${data.focused ? "focused" : ""}`}>
      <Handle id="up" type="target" position={Position.Top} />
      <Handle id="in" type="target" position={Position.Left} />
      <div className="kicker">{data.kind === "agent" ? "✦ " : ""}{data.kicker}</div>
      <div className="name">{data.name}</div>
      <div className={`trace-chip ${data.chip.toLowerCase()}`}><i />{data.chip}</div>
      <Handle id="out" type="source" position={Position.Right} />
      <Handle id="down" type="source" position={Position.Bottom} />
      <Handle id="up-out" type="source" position={Position.Top} />
      <Handle id="down-in" type="target" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { trace: TraceNode };

function FitPipeline({ token }: { token: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      fitView({ padding: 0.16, duration: 160, maxZoom: 1.15 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [token, fitView]);
  useEffect(() => {
    const canvas = document.querySelector(".trace-canvas");
    if (!canvas) return;
    let timer = 0;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fitView({ padding: 0.16, duration: 0, maxZoom: 1.15 }), 40);
    });
    observer.observe(canvas);
    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, [fitView]);
  return null;
}

export function FlowMap({
  events,
  status,
  focusId,
  onPick,
}: {
  events: LiveEvent[];
  status?: string | null;
  focusId?: string | null;
  onPick: (event: LiveEvent | null, meta: { name: string; chip: string }) => void;
}) {
  const chips = useMemo(() => {
    const map = new Map<string, Chip>();
    SPECS.forEach((spec) => map.set(spec.id, chipFor(spec, events, status)));
    return map;
  }, [events, status]);

  const nodes: Node[] = useMemo(
    () =>
      SPECS.map((spec) => {
        const chip = chips.get(spec.id) || "Unavailable";
        return {
          id: spec.id,
          type: "trace",
          position: { x: spec.x, y: spec.y },
          data: { name: spec.name, kicker: spec.kicker, kind: spec.kind, chip, active: chip === "Waiting" || chip === "Running", focused: spec.id === focusId },
          draggable: false,
          style: { width: 168 },
        };
      }),
    [chips, focusId],
  );

  const edges: Edge[] = LINKS.map((link) => {
    const reached = (chip: Chip) => chip === "Done" || chip === "Running" || chip === "Waiting";
    const live = reached(chips.get(link.source) || "Unavailable") && reached(chips.get(link.target) || "Unavailable");
    const color = live ? "#1f8a84" : "#b7c4c1";
    return {
      id: `${link.source}-${link.target}`,
      source: link.source,
      target: link.target,
      sourceHandle: link.from,
      targetHandle: link.to,
      type: "smoothstep",
      pathOptions: { borderRadius: 16 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
      style: {
        stroke: color,
        strokeWidth: live ? 1.6 : 1.2,
        strokeDasharray: live ? undefined : "5 5",
      },
    };
  });

  return (
    <div className="trace">
      <div className="trace-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.16, maxZoom: 1.15 }}
          minZoom={0.4}
          maxZoom={1.35}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_event, node) => {
            const spec = SPECS.find((item) => item.id === node.id);
            if (!spec) return;
            onPick(pickEvent(spec, events) || null, { name: spec.name, chip: chips.get(spec.id) || "Unavailable" });
          }}
        >
          <FitPipeline token={`${status || ""}:${events.length}:${events[events.length - 1]?.id || ""}`} />
          <Background variant={BackgroundVariant.Dots} gap={16} size={1.2} color="#c5d1ce" />
          <Controls showInteractive={false} position="top-left" />
          <MiniMap
            position="bottom-right"
            style={{ width: 104, height: 64 }}
            pannable
            zoomable
            maskColor="rgba(244, 246, 245, 0.72)"
            nodeColor={(node) => {
              const kind = (node.data as { kind?: Kind }).kind;
              if (kind === "human") return "#067647";
              if (kind === "agent") return "#8ec9c3";
              return "#d5dedc";
            }}
          />
        </ReactFlow>
      </div>
      <div className="trace-legend">
        <span><i className="swatch tool" /> Tool</span>
        <span><i className="swatch agent" /> AI agent</span>
        <span><i className="swatch human" /> Human review gate</span>
        <span><i className="swatch dashed" /> Dashed: not connected or not run</span>
      </div>
    </div>
  );
}
