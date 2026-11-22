import assert from "node:assert/strict";
import test, { beforeEach } from "node:test";

// A small stand-in for the browser: storage, a location, and a fetch whose answers each test chooses.
const store = new Map<string, string>();
const location = { pathname: "/c/home", href: "/c/home", protocol: "http:", hostname: "example.test" };
(globalThis as any).localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
(globalThis as any).window = { location };
const calls: { url: string; headers: Headers; body?: unknown }[] = [];
let answers: ((url: string) => Response)[] = [];
(globalThis as any).fetch = async (url: string, init: RequestInit = {}) => {
  calls.push({ url, headers: new Headers(init.headers), body: init.body });
  const next = answers.shift();
  if (!next) throw new Error("unexpected request " + url);
  return next(url);
};
const json = (status: number, body: unknown) => () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const api = await import("../lib/api.ts");

beforeEach(async () => {
  await new Promise((r) => setTimeout(r, 5)); // let the last test's shared refresh clear itself
  store.clear();
  calls.length = 0;
  answers = [];
  location.pathname = "/c/home";
  location.href = "/c/home";
});

test("the two portals keep separate tokens", () => {
  api.setToken("operator", "op-token", "op-refresh");
  api.setToken("customer", "cu-token");
  assert.equal(api.getToken("operator"), "op-token");
  assert.equal(api.getToken("customer"), "cu-token");
  api.clearToken("operator");
  assert.equal(api.getToken("operator"), "");
  assert.equal(api.getToken("customer"), "cu-token");
  assert.equal(store.has("payshield_operator_refresh"), false);
});

test("a stored user comes back, and a broken one is ignored", () => {
  api.setUser("customer", { id: "cus_1" });
  assert.deepEqual(api.getUser("customer"), { id: "cus_1" });
  store.set(api.userKey("customer"), "{not json");
  assert.equal(api.getUser("customer"), null);
  assert.equal(api.getUser("operator"), null);
});

test("a request carries the right portal's token and goes through the /backend proxy", async () => {
  api.setToken("customer", "cu-token");
  answers = [json(200, { ok: true })];
  assert.deepEqual(await api.api("/api/customer/home", { portal: "customer" }), { ok: true });
  assert.equal(calls[0].url, "/backend/api/customer/home");
  assert.equal(calls[0].headers.get("authorization"), "Bearer cu-token");
});

test("a body is sent as JSON, and an explicit Authorization header is respected", async () => {
  api.setToken("operator", "op-token");
  answers = [json(200, {}), json(200, {})];
  await api.api("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
  assert.equal(calls[0].headers.get("content-type"), "application/json");
  await api.api("/api/x", { headers: { Authorization: "Bearer other" } });
  assert.equal(calls[1].headers.get("authorization"), "Bearer other");
});

test("an error answer becomes an ApiError with the server's message and status", async () => {
  answers = [json(422, { detail: "The two passwords are not the same." })];
  await assert.rejects(api.api("/api/x"), (err: any) => err instanceof api.ApiError && err.status === 422 && err.message === "The two passwords are not the same.");
  answers = [() => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" })];
  await assert.rejects(api.api("/api/x"), (err: any) => err.status === 502 && err.message === "Bad Gateway");
  answers = [json(400, { detail: [{ msg: "field required" }] })];
  await assert.rejects(api.api("/api/x"), (err: any) => err.status === 400 && typeof err.message === "string");
});

test("an expired access token is renewed once with the refresh token and the request is retried", async () => {
  api.setToken("customer", "old", "refresh-1");
  answers = [json(401, { detail: "expired" }), json(200, { token: "new" }), json(200, { ok: 1 })];
  assert.deepEqual(await api.api("/api/customer/home", { portal: "customer" }), { ok: 1 });
  assert.equal(calls[1].url, "/backend/api/auth/refresh");
  assert.equal(api.getToken("customer"), "new");
  assert.equal(calls[2].headers.get("authorization"), "Bearer new");
});

test("several requests that expire together share one refresh", async () => {
  api.setToken("customer", "old", "refresh-1");
  let refreshes = 0;
  (globalThis as any).fetch = async (url: string, init: RequestInit = {}) => {
    const h = new Headers(init.headers);
    if (url.endsWith("/auth/refresh")) { refreshes++; return json(200, { token: "new" })(url); }
    return h.get("authorization") === "Bearer new" ? json(200, { ok: 1 })(url) : json(401, { detail: "expired" })(url);
  };
  try {
    await Promise.all([api.api("/api/a", { portal: "customer" }), api.api("/api/b", { portal: "customer" }), api.api("/api/c", { portal: "customer" })]);
    assert.equal(refreshes, 1);
  } finally {
    (globalThis as any).fetch = async (url: string, init: RequestInit = {}) => {
      calls.push({ url, headers: new Headers(init.headers), body: init.body });
      const next = answers.shift();
      if (!next) throw new Error("unexpected request " + url);
      return next(url);
    };
  }
});

test("when the refresh fails the person is signed out and sent to their own login page", async () => {
  api.setToken("customer", "old", "refresh-1");
  answers = [json(401, {}), json(401, {})];
  await assert.rejects(api.api("/api/customer/home", { portal: "customer" }));
  assert.equal(api.getToken("customer"), "");
  assert.equal(location.href, "/c/login");
  api.setToken("operator", "old");
  answers = [json(401, {})];
  await assert.rejects(api.api("/api/overview"));
  assert.equal(location.href, "/signin");
});

test("a wrong current password is shown as an error and does not sign the customer out", async () => {
  api.setToken("customer", "good", "refresh-1");
  answers = [json(401, { detail: "Your current password is not correct." })];
  await assert.rejects(api.api("/api/customer/password", { portal: "customer", method: "POST", body: "{}" }), (err: any) => err.message === "Your current password is not correct.");
  assert.equal(api.getToken("customer"), "good");
  assert.equal(location.href, "/c/home");
  assert.equal(calls.length, 1, "no refresh was attempted");
});

test("a failed sign-in is an error on the form, not a redirect", async () => {
  answers = [json(401, { detail: "Incorrect username or password." })];
  await assert.rejects(api.api("/api/auth/login", { method: "POST", body: "{}" }), (err: any) => err.status === 401);
  assert.equal(location.href, "/c/home");
});

test("already on the login page, a 401 does not loop back to it", async () => {
  location.pathname = "/c/login";
  location.href = "/c/login?x=1";
  api.setToken("customer", "old");
  answers = [json(401, {})];
  await assert.rejects(api.api("/api/customer/home", { portal: "customer" }));
  assert.equal(location.href, "/c/login?x=1");
});

test("the live-stream address uses the page's host, the right scheme, and encodes the token", () => {
  api.setToken("operator", "a b/c");
  delete process.env.NEXT_PUBLIC_WS_URL;
  delete process.env.NEXT_PUBLIC_WS_PORT;
  assert.equal(api.wsUrl("operator"), "ws://example.test:8000/ws?token=a%20b%2Fc");
  location.protocol = "https:";
  process.env.NEXT_PUBLIC_WS_PORT = "9000";
  assert.equal(api.wsUrl("operator"), "wss://example.test:9000/ws?token=a%20b%2Fc");
  process.env.NEXT_PUBLIC_WS_URL = "wss://stream.example/ws";
  assert.equal(api.wsUrl("operator"), "wss://stream.example/ws?token=a%20b%2Fc");
  process.env.NEXT_PUBLIC_WS_URL = "wss://stream.example/ws?room=1";
  assert.equal(api.wsUrl("operator"), "wss://stream.example/ws?room=1&token=a%20b%2Fc");
  location.protocol = "http:";
  delete process.env.NEXT_PUBLIC_WS_URL;
  delete process.env.NEXT_PUBLIC_WS_PORT;
});
