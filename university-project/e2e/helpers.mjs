import { chromium } from "playwright";

export const API = process.env.E2E_API || "http://localhost:8000";
export const WEB = process.env.E2E_WEB || "http://localhost:3000";
export const PASSWORD = "Harbour-2468";

// A fake camera, so the face scan runs without a person in front of it.
export const launch = () =>
  chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });

export async function operatorToken(email = "analyst@payshield.lk", password = "pilot-analyst") {
  const reply = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
  if (!reply.ok) throw new Error(`operator login failed: ${reply.status}`);
  return (await reply.json()).token;
}

export async function call(token, method, path, body) {
  const reply = await fetch(API + path, { method, headers: { "content-type": "application/json", Authorization: `Bearer ${token}` }, body: body ? JSON.stringify(body) : undefined });
  const text = await reply.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: reply.status, json, text };
}

export async function assist(token, customerId) {
  return (await call(token, "GET", `/api/demo/assist?customer_id=${customerId}`)).json;
}

export const reset = (token) => call(token, "POST", "/api/demo/reset");
export const unregister = (token, customerId) => call(token, "POST", "/api/demo/unregister", { customer_id: customerId });
export const trigger = async (token, customerId, scenario) => (await call(token, "POST", "/api/demo/trigger", { customer_id: customerId, scenario })).json.case_id;

/** Watches a page for script errors and failed same-site requests that are not expected. */
export function watch(page) {
  const problems = [];
  page.on("pageerror", (e) => problems.push("script error: " + e.message));
  page.on("response", (r) => {
    if (r.status() >= 500) problems.push(`${r.status()} ${r.url()}`);
  });
  return problems;
}

export async function fillRegistration(page, rec, overrides = {}) {
  const v = { full_name: rec.full_name, email: rec.email, nic: rec.nic, phone: rec.phone, account_number: rec.account_number, ...overrides };
  await page.fill("#n", v.full_name);
  await page.fill("#e", v.email);
  await page.fill("#i", v.nic);
  await page.fill("#m", v.phone);
  await page.fill("#a", v.account_number);
}

export async function registerThroughTheSite(page, rec, password = PASSWORD) {
  await page.goto(`${WEB}/c/register`);
  await fillRegistration(page, rec);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.fill("#p", password);
  await page.fill("#c", password);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /start face scan/i }).click();
  const outcome = await Promise.race([
    page.waitForURL("**/c/home", { timeout: 60000 }).then(() => "home"),
    page.locator(".cs-error").waitFor({ timeout: 60000 }).then(async () => "error: " + (await page.locator(".cs-error").innerText())),
  ]);
  if (outcome !== "home") throw new Error("registration did not finish: " + outcome);
}

export async function signInCustomer(page, username, password = PASSWORD) {
  await page.goto(`${WEB}/c/login`);
  await page.fill("#username", username);
  await page.fill("#password", password);
  await page.getByRole("button", { name: "Log in" }).click();
}

export async function overflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
}
