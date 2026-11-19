export type Portal = "operator" | "customer";

const KEYS = { operator: "payshield_operator", customer: "payshield_customer" };

export function getToken(portal: Portal = "operator"): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(KEYS[portal]) || "";
}

export function setToken(portal: Portal, token: string, refresh?: string) {
  localStorage.setItem(KEYS[portal], token);
  if (refresh) localStorage.setItem(KEYS[portal] + "_refresh", refresh);
}

export function clearToken(portal: Portal) {
  localStorage.removeItem(KEYS[portal]);
  localStorage.removeItem(KEYS[portal] + "_refresh");
}

// Access tokens last 30 minutes. One shared refresh call per portal renews them.
const refreshing: Partial<Record<Portal, Promise<boolean>>> = {};

async function renewAccess(portal: Portal): Promise<boolean> {
  const refresh = localStorage.getItem(KEYS[portal] + "_refresh");
  if (!refresh) return false;
  if (!refreshing[portal]) {
    refreshing[portal] = (async () => {
      try {
        const response = await fetch("/backend/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!response.ok) return false;
        const body = (await response.json()) as { token?: string };
        if (!body.token) return false;
        localStorage.setItem(KEYS[portal], body.token);
        return true;
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

export async function api<T = unknown>(
  path: string,
  options: RequestInit & { portal?: Portal; raw?: boolean } = {},
): Promise<T> {
  const portal = options.portal || "operator";
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const explicitAuth = headers.has("Authorization");
  const token = getToken(portal);
  if (token && !explicitAuth) headers.set("Authorization", `Bearer ${token}`);
  let response = await fetch(`/backend${path}`, { ...options, headers });
  if (response.status === 401 && typeof window !== "undefined" && !explicitAuth && !path.startsWith("/api/auth/")) {
    if (await renewAccess(portal)) {
      headers.set("Authorization", `Bearer ${getToken(portal)}`);
      response = await fetch(`/backend${path}`, { ...options, headers });
    }
  }
  if (response.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    clearToken(portal);
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

export function wsUrl(portal: Portal = "operator"): string {
  const token = getToken(portal);
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return `${explicit}${explicit.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const port = process.env.NEXT_PUBLIC_WS_PORT || "8000";
  return `${proto}://${window.location.hostname}:${port}/ws?token=${encodeURIComponent(token)}`;
}
