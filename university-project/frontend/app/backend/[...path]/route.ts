export const dynamic = "force-dynamic";

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
  const init: RequestInit = { method: req.method, headers, redirect: "manual" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }
  const response = await fetch(url, init);
  const out = new Headers();
  response.headers.forEach((value, key) => {
    if (!HOP.has(key.toLowerCase())) out.set(key, value);
  });
  return new Response(response.body, { status: response.status, headers: out });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
