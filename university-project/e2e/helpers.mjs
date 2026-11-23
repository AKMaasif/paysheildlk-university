import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
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

// The authenticator secrets the presenter would add to a phone app, remembered when Demo control shows them.
const secrets = new Map();
const nameKey = (name) => name.trim().toLowerCase().replace(/\s+/g, " ");

export async function assist(token, customerId) {
  const body = (await call(token, "GET", `/api/demo/assist?customer_id=${customerId}`)).json;
  if (body?.authenticator?.secret) secrets.set(nameKey(body.full_name), body.authenticator.secret);
  return body;
}

function base32(text) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of text.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** The 6-digit code an authenticator app shows for a secret (RFC 6238, 30-second steps), `offset` steps from now. */
export function totp(secret, offset = 0, at = Date.now()) {
  const counter = Math.floor(at / 30000) + offset;
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const hmac = crypto.createHmac("sha1", base32(secret)).update(msg).digest();
  const start = hmac[19] & 0xf;
  const value = ((hmac[start] & 0x7f) << 24) | (hmac[start + 1] << 16) | (hmac[start + 2] << 8) | hmac[start + 3];
  return String(value % 1000000).padStart(6, "0");
}

// The server accepts a code once. Like a person waiting for the app to show a new one, pick a code of the accepted
// window that has not been used in the last 90 seconds, and wait for the next window if all three are used. Spec files
// run as separate processes, so the used codes are kept in a small shared file.
const usedFile = path.join(os.tmpdir(), "payshield-e2e-used-codes.json");
function readUsed() {
  try { return JSON.parse(fs.readFileSync(usedFile, "utf8")); } catch { return {}; }
}
export async function freshCode(secret) {
  for (;;) {
    const now = Date.now();
    const used = Object.fromEntries(Object.entries(readUsed()).filter(([, at]) => now - at <= 90000));
    for (const offset of [0, 1, -1]) {
      const code = totp(secret, offset, now);
      if (!used[secret + code]) {
        used[secret + code] = now;
        fs.writeFileSync(usedFile, JSON.stringify(used));
        return code;
      }
    }
    fs.writeFileSync(usedFile, JSON.stringify(used));
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

export async function enterCode(page, secret) {
  await page.waitForSelector("#code", { timeout: 20000 });
  await page.fill("#code", await freshCode(secret));
  await page.locator("form:has(#code) button[type=submit]").click();
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

// The username the tests choose at registration: the name with dots, for example nimal.perera.
export const usernameFor = (fullName) => fullName.trim().toLowerCase().replace(/\s+/g, ".");

export async function registerThroughTheSite(page, rec, password = PASSWORD) {
  await page.goto(`${WEB}/c/register`);
  await fillRegistration(page, rec);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.fill("#u", usernameFor(rec.full_name));
  await page.fill("#p", password);
  await page.fill("#c", password);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /start face scan/i }).click();
  // After the scan the site asks the customer to add the account to an authenticator app and confirm with a code.
  const enrol = await Promise.race([
    page.waitForSelector("#code", { timeout: 60000 }).then(() => "code"),
    page.locator(".cs-error").waitFor({ timeout: 60000 }).then(async () => "error: " + (await page.locator(".cs-error").innerText())),
  ]);
  if (enrol !== "code") throw new Error("registration did not finish: " + enrol);
  await enterCode(page, rec.authenticator.secret);
  const outcome = await Promise.race([
    page.waitForURL("**/c/home", { timeout: 60000 }).then(() => "home"),
    page.locator(".cs-error").waitFor({ timeout: 60000 }).then(async () => "error: " + (await page.locator(".cs-error").innerText())),
  ]);
  if (outcome !== "home") throw new Error("registration did not finish: " + outcome);
}

export async function signInCustomer(page, username, password = PASSWORD) {
  await page.goto(`${WEB}/c/login`);
  await page.fill("#username", usernameFor(username)); // callers pass the customer's name; registration chose name.with.dots
  await page.fill("#password", password);
  await page.getByRole("button", { name: "Log in" }).click();
  // A right password leads to the authenticator code screen. A wrong one leaves the error on the password screen.
  const next = await Promise.race([
    page.waitForSelector("#code", { timeout: 15000 }).then(() => "code").catch(() => "none"),
    page.locator(".cs-error").waitFor({ timeout: 15000 }).then(() => "error").catch(() => "none"),
  ]);
  if (next === "code") {
    const secret = secrets.get(nameKey(username));
    if (!secret) throw new Error(`no authenticator secret remembered for ${username}; call assist() for this customer first`);
    await enterCode(page, secret);
  }
}

export async function overflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
}
