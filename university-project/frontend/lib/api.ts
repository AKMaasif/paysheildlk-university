export type Portal = "operator" | "customer";

const KEYS = { operator: "payshield_operator", customer: "payshield_customer" };

// The session itself lives in HttpOnly cookies that page scripts cannot read: the server sets them when the person signs
// in and the browser sends them with every request. What is kept here is only the person's public details (name, role),
// used to draw the page and to know whether to show the sign-in screen. It is not a credential.
export function isSignedIn(portal: Portal = "operator"): boolean {
  if (typeof window === "undefined") return false;
  return !!localStorage.getItem(userKey(portal));
}

/** Forget the person's details here and clear the session cookies on the server. */
export async function clearSession(portal: Portal): Promise<void> {
  localStorage.removeItem(userKey(portal));
  try {
    await fetch("/backend/api/auth/logout", { method: "POST", headers: SITE_HEADERS, body: JSON.stringify({ portal }) });
  } catch {
    /* the cookies expire on their own */
  }
}

// Sent on every request. The first asks the server for cookie sessions at sign-in; the second proves a state-changing
// request comes from this site and not another page (a page on another site cannot add it without our permission).
const SITE_HEADERS = { "X-Session-Mode": "cookie", "X-Requested-With": "payshield", "Content-Type": "application/json" };

// The access cookie lasts 30 minutes. One shared refresh call per portal renews it from the refresh cookie.
const refreshing: Partial<Record<Portal, Promise<boolean>>> = {};

async function renewAccess(portal: Portal): Promise<boolean> {
  if (!refreshing[portal]) {
    refreshing[portal] = (async () => {
      try {
        const response = await fetch("/backend/api/auth/refresh", { method: "POST", headers: SITE_HEADERS, body: JSON.stringify({ portal }) });
        return response.ok;
      } catch {
        return false;
      } finally {
        setTimeout(() => { delete refreshing[portal]; }, 0);
      }
    })();
  }
  return refreshing[portal] as Promise<boolean>;
}

export function userKey(portal: Portal) {
  return KEYS[portal] + "_user";
}

export function getUser<T>(portal: Portal = "operator"): T | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(userKey(portal));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function setUser(portal: Portal, user: unknown) {
  localStorage.setItem(userKey(portal), JSON.stringify(user));
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// These answer 401 for "the password you typed is wrong", not "your session ended".
const credentialCheck = (path: string) => path.startsWith("/api/auth/") || path.startsWith("/api/customer/totp/") || path === "/api/customer/password";

export async function api<T = unknown>(
  path: string,
  options: RequestInit & { portal?: Portal; raw?: boolean } = {},
): Promise<T> {
  const portal = options.portal || "operator";
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("X-Session-Mode", "cookie");
  headers.set("X-Requested-With", "payshield");
  const explicitAuth = headers.has("Authorization");
  let response = await fetch(`/backend${path}`, { ...options, headers });
  if (response.status === 401 && typeof window !== "undefined" && !explicitAuth && !credentialCheck(path)) {
    if (await renewAccess(portal)) response = await fetch(`/backend${path}`, { ...options, headers });
  }
  if (response.status === 401 && typeof window !== "undefined" && !credentialCheck(path)) {
    localStorage.removeItem(userKey(portal));
    const target = portal === "customer" ? "/c/login" : "/signin";
    if (!window.location.pathname.startsWith(target)) window.location.href = target;
  }
  if (options.raw) return response as T;
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch {
      /* keep status text */
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return (await response.json()) as T;
  return (await response.text()) as T;
}

export async function downloadAuthed(path: string, filename: string, portal: Portal = "operator") {
  // Goes through api() so an expired access token is renewed first.
  const response = await api<Response>(path, { portal, raw: true });
  if (!response.ok) throw new ApiError(response.status, "Download failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

// The live stream signs in with the same cookie (the browser sends it with the connection). The server only accepts the
// cookie from a page of this site, and `portal` says which of the two sessions this connection is for.
export function wsUrl(portal: Portal = "operator"): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return `${explicit}${explicit.includes("?") ? "&" : "?"}portal=${portal}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const port = process.env.NEXT_PUBLIC_WS_PORT || "8000";
  return `${proto}://${window.location.hostname}:${port}/ws?portal=${portal}`;
}
