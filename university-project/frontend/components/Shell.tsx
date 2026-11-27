"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Icon, Logo } from "@/components/Icons";
import { api, clearSession, getUser, isSignedIn, wsUrl } from "@/lib/api";
import { clock } from "@/lib/format";

export type Operator = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  role_label: string;
  bank: string | null;
  scope: string;
};

export type DetailSection = { label: string; value: React.ReactNode };
export type DetailModel = { title: string; subtitle?: string; sections: DetailSection[] } | null;

type ShellContextValue = {
  user: Operator | null;
  query: string;
  refreshKey: number;
  refresh: () => void;
  clearEvents: () => void;
  setDetails: (detail: DetailModel) => void;
  health: Health | null;
  events: LiveEvent[];
};

export type LiveEvent = {
  id: string;
  case_id: string;
  customer_id?: string;
  bank?: string;
  agent: string;
  state: string;
  tool_name?: string | null;
  tool_args?: Record<string, unknown> | null;
  result_summary?: string | null;
  handoff_from?: string | null;
  handoff_to?: string | null;
  provider?: string | null;
  model?: string | null;
  fell_back?: boolean;
  fallback_from?: string | null;
  latency_ms?: number | null;
  message?: string | null;
  created_at: string;
  // A tool call is a "started" event and then a "finished" or "failed" one with the same call_id. "retrying" marks a repeat.
  phase?: "started" | "finished" | "failed" | "retrying" | null;
  call_id?: string | null;
  result?: Record<string, unknown> | null;
  meta?: { planner?: string; why?: string; run_id?: string | null; transaction_id?: string | null; attempt?: number; attempts?: number; delivery_state?: string } | null;
};

type Health = {
  ok: boolean;
  redis: string;
  sms: string;
  email?: string;
  telco?: string;
  mode?: string;
  mode_note?: string;
  ignored_by_mode?: string[];
  recipients_cleared?: { phones: number; emails: number };
  realert_seconds?: number;
  escalate_seconds?: number;
  llm: { primary: string; meta_configured: boolean; grok_configured: boolean };
};

type Severity = { crit: number; high: number; med: number; low: number };

const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell() {
  const value = useContext(ShellContext);
  if (!value) throw new Error("Shell missing");
  return value;
}

const ROLES = ["fraud_analyst", "telecom_operator", "bank_supervisor", "auditor", "super_admin"];

const SECTIONS: { label: string; items: { href: string; label: string; icon: string; roles: string[]; badge?: boolean }[] }[] = [
  {
    label: "Monitor",
    items: [
      { href: "/overview", label: "Overview", icon: "overview", roles: ROLES },
      { href: "/live", label: "Live activity", icon: "live", roles: ROLES, badge: true },
      { href: "/analytics", label: "Fraud analytics", icon: "report", roles: ROLES },
      { href: "/map", label: "Sri Lanka map", icon: "map", roles: ROLES },
      { href: "/sim-events", label: "SIM events", icon: "sim", roles: ROLES },
    ],
  },
  {
    label: "Respond",
    items: [
      { href: "/cases", label: "Cases", icon: "cases", roles: ROLES },
      { href: "/holds", label: "On hold", icon: "hold", roles: ["fraud_analyst", "bank_supervisor", "auditor", "super_admin"] },
      { href: "/reports", label: "Reports", icon: "report", roles: ROLES },
    ],
  },
  {
    label: "Assure",
    items: [
      { href: "/agents", label: "Agents & models", icon: "model", roles: ROLES },
      { href: "/simulate", label: "Simulate", icon: "demo", roles: ROLES },
      { href: "/calibration", label: "Calibration", icon: "report", roles: ROLES },
      { href: "/enrol", label: "Add customer", icon: "cases", roles: ["fraud_analyst", "super_admin", "bank_supervisor"] },
      { href: "/security", label: "Security", icon: "settings", roles: ROLES },
      { href: "/admin", label: "Administration", icon: "admin", roles: ["super_admin"] },
      { href: "/settings", label: "Settings", icon: "settings", roles: ROLES },
    ],
  },
];

const TITLES: Record<string, string> = {
  "/overview": "Overview",
  "/live": "Live activity",
  "/cases": "Cases",
  "/sim-events": "SIM events",
  "/holds": "On hold",
  "/map": "Sri Lanka map",
  "/agents": "Agents & models",
  "/reports": "Reports",
  "/admin": "Administration",
  "/settings": "Settings",
  "/demo": "Demo controls",
  "/simulate": "Simulate",
  "/calibration": "Rule calibration",
  "/analytics": "Fraud analytics",
  "/enrol": "Add customer",
  "/security": "Security",
};

export function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Operator | null>(null);
  const [query, setQuery] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [details, setDetails] = useState<DetailModel>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [severity, setSeverity] = useState<Severity>({ crit: 0, high: 0, med: 0, low: 0 });
  const [waiting, setWaiting] = useState(0);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [now, setNow] = useState("");

  useEffect(() => {
    if (!isSignedIn("operator")) {
      router.replace("/signin");
      return;
    }
    setUser(getUser<Operator>());
    setReady(true);
  }, [router]);

  useEffect(() => {
    setDetails(null);
  }, [pathname]);

  useEffect(() => {
    const base = pathname.startsWith("/cases/") ? "Case" : TITLES[pathname] || "PayShield";
    document.title = `PayShield — ${base}`;
  }, [pathname]);

  useEffect(() => {
    let stop = false;
    const load = () => {
      api<Health>("/api/health").then((body) => { if (!stop) setHealth(body); }).catch(() => { if (!stop) setHealth(null); });
      api<{ kpis: { waiting: number; severity?: Severity } }>("/api/overview")
        .then((body) => {
          if (stop) return;
          setWaiting(body.kpis.waiting || 0);
          if (body.kpis.severity) setSeverity(body.kpis.severity);
        })
        .catch(() => undefined);
    };
    load();
    const timer = setInterval(load, 15000);
    const clockTimer = setInterval(() => setNow(clock()), 1000);
    setNow(clock());
    return () => { stop = true; clearInterval(timer); clearInterval(clockTimer); };
  }, [refreshKey]);

  useEffect(() => {
    if (!ready) return;
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;
    const connect = () => {
      socket = new WebSocket(wsUrl("operator"));
      socket.onmessage = (message) => {
        const body = JSON.parse(message.data);
        if (body.type === "snapshot" && Array.isArray(body.events)) {
          setEvents(body.events.slice(-400));
        } else if (body.type === "event" && body.event) {
          setEvents((current) => [...current.slice(-399), body.event]);
        }
      };
      socket.onclose = () => {
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, [ready]);

  const value = useMemo<ShellContextValue>(
    () => ({
      user,
      query,
      refreshKey,
      refresh: () => setRefreshKey((n) => n + 1),
      clearEvents: () => setEvents([]),
      setDetails,
      health,
      events,
    }),
    [user, query, refreshKey, health, events],
  );

  if (!ready) return <div className="signin-page">Opening PayShield…</div>;

  function signOut() {
    void clearSession("operator");
    router.replace("/signin");
  }

  const offline = !health?.llm.meta_configured && !health?.llm.grok_configured;
  const engineLabel = offline
    ? "OFFLINE RULES ENGINE"
    : health?.llm.meta_configured
      ? health.llm.grok_configured
        ? "META · GROK FALLBACK"
        : "META"
      : "GROK";
  const initials = (user?.full_name || "?")
    .split(" ")
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
  const smsLabel = health?.sms === "textlk" ? "Text.lk" : "log-only";
  const telcoLabel = health?.telco || "simulated CAMARA";
  const emailLabel = health?.email === "smtp" ? "SMTP" : "log-only";
  const modeLabel = health?.mode || "deterministic";
  const modeDetail = [health?.mode_note, health?.ignored_by_mode?.length ? `Credentials present but ignored in this mode: ${health.ignored_by_mode.join(", ")}.` : "", health?.recipients_cleared ? `Test recipients cleared: ${health.recipients_cleared.phones} phone(s), ${health.recipients_cleared.emails} email(s).` : ""].filter(Boolean).join(" ");

  return (
    <ShellContext.Provider value={value}>
      <div className="console">
        <aside className="side">
          <nav>
            {SECTIONS.map((section) => {
              const items = section.items.filter((item) => !user || item.roles.includes(user.role));
              if (!items.length) return null;
              return (
                <div key={section.label}>
                  <div className="side-label">{section.label}</div>
                  {items.map((item) => {
                    const active = pathname === item.href || (item.href !== "/overview" && pathname.startsWith(item.href));
                    return (
                      <Link key={item.href} href={item.href} className={`side-link ${active ? "active" : ""}`}>
                        <Icon name={item.icon} size={16} />
                        {item.label}
                        {item.badge && waiting > 0 && <span className="badge">{waiting}</span>}
                      </Link>
                    );
                  })}
                </div>
              );
            })}
          </nav>
          {user?.role !== "auditor" && (
            <Link href="/demo" className={`demo-pin ${pathname === "/demo" ? "active" : ""}`}>
              <Icon name="demo" size={16} />
              Demo controls
            </Link>
          )}
          <div className="side-foot">{user?.bank ? `Scope: ${user.bank}` : "10 fictional customers"}</div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <div className="brand">
              <Logo size={26} onDark />
              <strong>PayShield.lk</strong>
            </div>
            <span className="context">Sri Lanka · Simulated banking network</span>
            <input
              className="top-search"
              placeholder="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="sev">
              <span className="crit">{severity.crit} CRIT</span>
              <span className="high">{severity.high} HIGH</span>
              <span>{severity.med} MED</span>
              <span>{severity.low} LOW</span>
            </div>
            <span className={`engine-pill ${offline ? "offline" : ""}`}>{engineLabel}</span>
            <span className="clock">{now} SLT</span>
            <span className="who"><span className="avatar">{initials}</span>{user?.full_name}</span>
            <button className="signout" onClick={signOut}>Sign out</button>
          </header>
          <div className="body-row">
            <main className="main" tabIndex={0} aria-label="Page content">{children}</main>
            {details && (
              <aside className="details">
                <div className="details-head">
                  <span>Details</span>
                  <button aria-label="Close details" onClick={() => setDetails(null)}>×</button>
                </div>
                <div className="details-body">
                  <div className="kv"><div className="k">Selected</div><div className="v"><strong>{details.title}</strong></div></div>
                  {details.subtitle && <div className="kv"><div className="v">{details.subtitle}</div></div>}
                  {details.sections.map((section) => (
                    <div className="kv" key={section.label}>
                      <div className="k">{section.label}</div>
                      <div className="v">{section.value}</div>
                    </div>
                  ))}
                </div>
              </aside>
            )}
          </div>
          <footer className="statusbar">
            <span title={modeDetail} data-testid="demo-mode"><i className="dot" /> Mode <strong>{modeLabel}</strong></span>
            <span><i className="dot" /> Updated {now}</span>
            <span><i className="dot" /> Live events {health?.redis === "connected" ? "redis" : "in-process"}</span>
            <span><i className="dot" /> AI {offline ? "offline rules" : health?.llm.primary}</span>
            <span><i className="dot" /> Telco {telcoLabel}</span>
            <span><i className="dot" /> SMS {smsLabel}</span>
            <span><i className="dot" /> Email {emailLabel}</span>
            <span className="spacer">{modeLabel === "production" ? "Live deployment." : "Demonstration. The bank ledger is synthetic and a SIM freeze is only recorded, never sent to an operator."}</span>
          </footer>
        </div>
      </div>
    </ShellContext.Provider>
  );
}

export function matchesQuery(query: string, values: Array<string | number | null | undefined>) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return values.some((value) => String(value ?? "").toLowerCase().includes(needle));
}
