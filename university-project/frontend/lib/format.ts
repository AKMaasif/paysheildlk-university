export function lkr(amount: number | null | undefined): string {
  return "Rs. " + Number(amount || 0).toLocaleString("en-US");
}

export function colombo(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const stamp = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Colombo",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${stamp} SLT`;
}

export function clock(now = new Date()): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Colombo",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);
}

export function prettyPhone(phone: string | null | undefined): string {
  if (!phone) return "";
  if (phone.startsWith("+94") && phone.length === 12) {
    const rest = phone.slice(3);
    return `+94 ${rest.slice(0, 2)} ${rest.slice(2, 5)} ${rest.slice(5)}`;
  }
  return phone;
}

export function statusLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    new: "New",
    running: "Running",
    waiting_customer: "Waiting for customer",
    waiting_verification: "Waiting for answers",
    escalating: "Escalating",
    escalated: "Escalated",
    open: "Open",
    closed_blocked: "Blocked",
    closed_released: "Released",
    closed_cleared: "Cleared",
    error: "Error",
    held: "Held",
    blocked: "Blocked",
    released: "Released",
    pending: "Pending",
    hold: "Hold",
    block: "Block",
    release: "Release",
  };
  return labels[status || ""] || status || "";
}

export function chipFor(value: string | null | undefined): string {
  const v = value || "";
  if (["high", "closed_blocked", "blocked", "block", "error"].includes(v)) return "high";
  if (["medium", "waiting_customer", "waiting_verification", "escalated", "escalating", "open", "held", "hold"].includes(v)) return "medium";
  if (["low", "closed_released", "closed_cleared", "released", "release"].includes(v)) return "ok";
  if (["running", "new"].includes(v)) return "info";
  return "neutral";
}

function asColombo(raw: string): string {
  let iso = raw.trim().replace(" ", "T").replace(/ UTC$/i, "Z");
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(iso)) iso += "Z";
  return colombo(iso) || raw;
}

export function humanizeResult(text: string | null | undefined): string {
  if (!text) return "";
  const swap = text.match(/checkSimSwap swapped=(true|false), latestSimChange=(.+)/i);
  let line = text;
  if (swap) {
    if (swap[1].toLowerCase() === "true") {
      const when = swap[2] === "none" ? "an unknown time" : asColombo(swap[2]);
      line = `SIM was swapped. Latest change ${when}.`;
    } else {
      line = "No SIM swap in the checked window.";
    }
  }
  return line.replace(
    /\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*(?:UTC|Z|[+-]\d{2}:\d{2})?/gi,
    (stamp) => asColombo(stamp.replace(/\s*UTC/i, "Z")),
  );
}

export function mmss(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}
