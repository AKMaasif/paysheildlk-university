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

test("the two portals are tracked separately, and the only thing kept is the person's public details", () => {
  assert.equal(api.isSignedIn("operator"), false);
  api.setUser("operator", { id: "op" });
  api.setUser("customer", { id: "cu" });
  assert.equal(api.isSignedIn("operator"), true);
  assert.equal(api.isSignedIn("customer"), true);
  store.delete(api.userKey("operator"));
  assert.equal(api.isSignedIn("operator"), false);
  assert.equal(api.isSignedIn("customer"), true);
  // no token of any kind is ever written to storage
  for (const key of store.keys()) assert.ok(!/token|refresh/i.test(key), key);
});

test("a stored user comes back, and a broken one is ignored", () => {
  api.setUser("customer", { id: "cus_1" });
  assert.deepEqual(api.getUser("customer"), { id: "cus_1" });
  store.set(api.userKey("customer"), "{not json");
  assert.equal(api.getUser("customer"), null);
  assert.equal(api.getUser("operator"), null);
});

test("a request goes through the /backend proxy asking for cookie sessions, with the site header and no token", async () => {
  api.setUser("customer", { id: "cu" });
  answers = [json(200, { ok: true })];
  assert.deepEqual(await api.api("/api/customer/home", { portal: "customer" }), { ok: true });
  assert.equal(calls[0].url, "/backend/api/customer/home");
  assert.equal(calls[0].headers.get("authorization"), null);
  assert.equal(calls[0].headers.get("x-session-mode"), "cookie");
  assert.equal(calls[0].headers.get("x-requested-with"), "payshield");
});

test("a body is sent as JSON, and an explicit Authorization header (the half-finished sign-in) is respected", async () => {
  answers = [json(200, {}), json(200, {})];
  await api.api("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
  assert.equal(calls[0].headers.get("content-type"), "application/json");
  await api.api("/api/x", { headers: { Authorization: "Bearer pending" } });
  assert.equal(calls[1].headers.get("authorization"), "Bearer pending");
});

test("an error answer becomes an ApiError with the server's message and status", async () => {
  answers = [json(422, { detail: "The two passwords are not the same." })];
  await assert.rejects(api.api("/api/x"), (err: any) => err instanceof api.ApiError && err.status === 422 && err.message === "The two passwords are not the same.");
  answers = [() => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" })];
  await assert.rejects(api.api("/api/x"), (err: any) => err.status === 502 && err.message === "Bad Gateway");
  answers = [json(400, { detail: [{ msg: "field required" }] })];
  await assert.rejects(api.api("/api/x"), (err: any) => err.status === 400 && typeof err.message === "string");
});

test("an expired access cookie is renewed once from the refresh cookie and the request is retried", async () => {
  api.setUser("customer", { id: "cu" });
  answers = [json(401, { detail: "expired" }), json(200, { session: "cookie" }), json(200, { ok: 1 })];
  assert.deepEqual(await api.api("/api/customer/home", { portal: "customer" }), { ok: 1 });
  assert.equal(calls[1].url, "/backend/api/auth/refresh");
  assert.deepEqual(JSON.parse(String(calls[1].body)), { portal: "customer" }); // no token travels in the request
  assert.equal(calls[1].headers.get("x-requested-with"), "payshield");
  assert.equal(calls[2].url, "/backend/api/customer/home");
  assert.equal(api.isSignedIn("customer"), true);
});

test("several requests that expire together share one refresh", async () => {
  api.setUser("customer", { id: "cu" });
  let refreshes = 0;
  let renewed = false;
  (globalThis as any).fetch = async (url: string) => {
    if (url.endsWith("/auth/refresh")) { refreshes++; renewed = true; return json(200, { session: "cookie" })(url); }
    return renewed ? json(200, { ok: 1 })(url) : json(401, { detail: "expired" })(url);
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
  api.setUser("customer", { id: "cu" });
  answers = [json(401, {}), json(401, {})];
  await assert.rejects(api.api("/api/customer/home", { portal: "customer" }));
  assert.equal(api.isSignedIn("customer"), false);
  assert.equal(location.href, "/c/login");
  api.setUser("operator", { id: "op" });
  answers = [json(401, {}), json(401, {})];
  await assert.rejects(api.api("/api/overview"));
  assert.equal(api.isSignedIn("operator"), false);
  assert.equal(location.href, "/signin");
});

test("a wrong current password is shown as an error and does not sign the customer out", async () => {
  api.setUser("customer", { id: "cu" });
  answers = [json(401, { detail: "Your current password is not correct." })];
  await assert.rejects(api.api("/api/customer/password", { portal: "customer", method: "POST", body: "{}" }), (err: any) => err.message === "Your current password is not correct.");
  assert.equal(api.isSignedIn("customer"), true);
  assert.equal(location.href, "/c/home");
  assert.equal(calls.length, 1, "no refresh was attempted");
});

test("a wrong authenticator code during set-up is an error on the form, not a sign-out", async () => {
  api.setUser("customer", { id: "cu" });
  answers = [json(401, { detail: "That code is not valid" })];
  await assert.rejects(api.api("/api/customer/totp/confirm", { portal: "customer", method: "POST", body: "{}" }), (err: any) => err.status === 401);
  assert.equal(api.isSignedIn("customer"), true);
  assert.equal(calls.length, 1);
});

test("a failed sign-in is an error on the form, not a redirect", async () => {
  answers = [json(401, { detail: "Incorrect username or password." })];
  await assert.rejects(api.api("/api/auth/login", { method: "POST", body: "{}" }), (err: any) => err.status === 401);
  assert.equal(location.href, "/c/home");
});

test("already on the login page, a 401 does not loop back to it", async () => {
  location.pathname = "/c/login";
  location.href = "/c/login?x=1";
  api.setUser("customer", { id: "cu" });
  answers = [json(401, {}), json(401, {})];
  await assert.rejects(api.api("/api/customer/home", { portal: "customer" }));
  assert.equal(location.href, "/c/login?x=1");
});

test("signing out forgets the person here and asks the server to clear the cookies for that portal only", async () => {
  api.setUser("customer", { id: "cu" });
  api.setUser("operator", { id: "op" });
  answers = [json(200, { ok: true })];
  await api.clearSession("customer");
  assert.equal(api.isSignedIn("customer"), false);
  assert.equal(api.isSignedIn("operator"), true);
  assert.equal(calls[0].url, "/backend/api/auth/logout");
  assert.deepEqual(JSON.parse(String(calls[0].body)), { portal: "customer" });
  assert.equal(calls[0].headers.get("x-requested-with"), "payshield");
});

test("signing out still forgets the person if the server cannot be reached", async () => {
  api.setUser("operator", { id: "op" });
  answers = [() => { throw new Error("offline"); }];
  await api.clearSession("operator");
  assert.equal(api.isSignedIn("operator"), false);
});

test("the live-stream address names the portal and carries no token", () => {
  delete process.env.NEXT_PUBLIC_WS_URL;
  delete process.env.NEXT_PUBLIC_WS_PORT;
  assert.equal(api.wsUrl("operator"), "ws://example.test:8000/ws?portal=operator");
  assert.equal(api.wsUrl("customer"), "ws://example.test:8000/ws?portal=customer");
  location.protocol = "https:";
  process.env.NEXT_PUBLIC_WS_PORT = "9000";
  assert.equal(api.wsUrl("operator"), "wss://example.test:9000/ws?portal=operator");
  process.env.NEXT_PUBLIC_WS_URL = "wss://stream.example/ws";
  assert.equal(api.wsUrl("operator"), "wss://stream.example/ws?portal=operator");
  process.env.NEXT_PUBLIC_WS_URL = "wss://stream.example/ws?room=1";
  assert.equal(api.wsUrl("customer"), "wss://stream.example/ws?room=1&portal=customer");
  location.protocol = "http:";
  delete process.env.NEXT_PUBLIC_WS_URL;
  delete process.env.NEXT_PUBLIC_WS_PORT;
});
