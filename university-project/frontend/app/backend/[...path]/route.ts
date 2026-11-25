export const dynamic = "force-dynamic";

const MAX_BODY = 8 * 1024 * 1024;
const HOP = new Set(["connection", "content-length", "transfer-encoding", "host"]);

async function proxy(req: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = process.env.API_PROXY_URL || "http://127.0.0.1:8000";
  const incoming = new URL(req.url);
  const url = `${base}/${path.join("/")}${incoming.search}`;
  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  // The backend sees this server as its only caller, so the visitor's address travels in X-Forwarded-For. Next.js fills
  // it from the connection when the browser sent none. The backend believes it only when this server is listed in
  // TRUSTED_PROXIES, and keeps a per-username limit that does not depend on it. Put a real proxy in front in production
  // and let it overwrite the header.
  headers.delete("x-real-ip");
  // The backend refuses bodies over 8 MB too; stopping them here saves reading them at all.
  if (Number(req.headers.get("content-length") || 0) > MAX_BODY) {
    return Response.json({ detail: "That request is too large." }, { status: 413 });
  }
  const init: RequestInit = { method: req.method, headers, redirect: "manual" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }
  const response = await fetch(url, init);
  const out = new Headers();
  response.headers.forEach((value, key) => {
    if (!HOP.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") out.set(key, value);
  });
  // Each Set-Cookie is its own header; copying them with set() would keep only the last.
  for (const cookie of response.headers.getSetCookie()) out.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
