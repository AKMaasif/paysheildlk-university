"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { LiveEvent } from "@/components/Shell";
import { terminalLines } from "@/lib/terminal";

type Filter = "all" | "tools" | "models" | "decisions";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "tools", label: "Tools" },
  { id: "models", label: "Models" },
  { id: "decisions", label: "Decisions" },
];

export function LiveTerminal({
  events,
  realertSeconds,
  escalateSeconds,
  onSelect,
}: {
  events: LiveEvent[];
  realertSeconds?: number;
  escalateSeconds?: number;
  onSelect: (event: LiveEvent, nodeId: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [follow, setFollow] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const [height, setHeight] = useState(290);
  const [clearedAt, setClearedAt] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => {
    const timers = { realert: realertSeconds || 180, escalate: escalateSeconds || 600 };
    return events.flatMap((event) => terminalLines(event, timers)).filter((line) => !clearedAt || line.event.created_at > clearedAt);
  }, [events, realertSeconds, escalateSeconds, clearedAt]);

  const visible = lines.filter((line) => filter === "all" || line.bucket === filter);

  useEffect(() => {
    if (!follow || !body.current) return;
    body.current.scrollTop = body.current.scrollHeight;
  }, [visible.length, follow, filter, collapsed]);

  function resize(event: React.MouseEvent) {
    event.preventDefault();
    const startY = event.clientY;
    const startH = height;
    const move = (ev: MouseEvent) => {
      setHeight(Math.min(520, Math.max(160, startH + (ev.clientY - startY))));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  async function copy() {
    const text = visible.map((line) => `${line.time} [${line.tag}] ${line.text}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className={`term ${collapsed ? "collapsed" : ""}`}>
      <header className="term-bar">
        <span className={`term-live ${follow ? "on" : ""}`} title="This only controls whether the view scrolls with new lines. The backend keeps working either way."><i />{follow ? "live" : "not following"}</span>
        <div className="term-filters">
          {FILTERS.map((item) => (
            <button key={item.id} className={filter === item.id ? "on" : ""} onClick={() => setFilter(item.id)}>{item.label}</button>
          ))}
        </div>
        <div className="term-actions">
          <button onClick={() => setFollow((value) => !value)} title="Stop or resume scrolling with new lines. Processing is not paused.">{follow ? "Stop following" : "Follow"}</button>
          <button onClick={() => setClearedAt(events.at(-1)?.created_at || new Date().toISOString())} title="Hides the lines in this view only. The case history is kept.">Clear view</button>
          <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          <button onClick={() => setCollapsed((value) => !value)}>{collapsed ? "Open" : "Hide"}</button>
        </div>
      </header>
      {!collapsed && (
        <div className="term-body" ref={body} style={{ height }}>
          {visible.length === 0 && <div className="term-empty">No lines for this filter.</div>}
          {visible.map((line) => (
            <button
              key={line.key}
              className={`term-line ${line.kind} ${picked === line.key ? "selected" : ""}`}
              onClick={() => { setPicked(line.key); onSelect(line.event, line.nodeId); }}
            >
              <span className="term-time">{line.time}</span>
              <span className="term-tag">[{line.tag}]</span>
              <span className="term-text">{line.text}</span>
            </button>
          ))}
        </div>
      )}
      {!collapsed && <div className="term-resize" onMouseDown={resize} role="separator" aria-label="Resize terminal" />}
    </section>
  );
}
