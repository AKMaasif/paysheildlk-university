import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { API, WEB, call, launch, operatorToken, reset, trigger, watch } from "../helpers.mjs";

const USERS = {
  fraud_analyst: ["analyst@payshield.lk", "pilot-analyst"],
  telecom_operator: ["telco@payshield.lk", "pilot-telco"],
  bank_supervisor: ["supervisor@payshield.lk", "pilot-supervisor"],
  auditor: ["auditor@payshield.lk", "pilot-auditor"],
  super_admin: ["admin@payshield.lk", "pilot-admin"],
};
const PAGES = ["overview", "cases", "holds", "live", "map", "analytics", "agents", "reports", "calibration", "simulate", "demo", "sim-events", "security", "settings", "enrol", "admin"];

let browser, token;
before(async () => {
  browser = await launch();
  token = await operatorToken();
  await reset(token);
});
after(async () => browser.close());

async function signIn(page, email, password) {
  await page.goto(`${WEB}/signin`);
  await page.getByLabel("Username").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.locator("button[type=submit]").click();
}

async function session(role, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const problems = watch(page);
  await signIn(page, ...USERS[role]);
  await page.waitForURL("**/overview", { timeout: 30000 });
  return { context, page, problems };
}

test("a wrong operator password is refused and a right one opens the console", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, "analyst@payshield.lk", "wrong-password");
  await page.locator(".form-error").first().waitFor();
  assert.ok(page.url().includes("/signin"));
  await signIn(page, ...USERS.fraud_analyst);
  await page.waitForURL("**/overview");
  await context.close();
});

for (const role of Object.keys(USERS)) {
  test(`${role} can sign in and sees the overview`, async () => {
    const { context, page, problems } = await session(role);
    await page.locator("h1").first().waitFor();
    assert.ok((await page.locator("body").innerText()).length > 200);
    assert.deepEqual(problems, []);
    await context.close();
  });
}

test("the console pages all load for a super admin without script errors or server errors", async () => {
  const { context, page, problems } = await session("super_admin");
  for (const name of PAGES) {
    await page.goto(`${WEB}/${name}`);
    await page.locator("h1").first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(400);
  }
  assert.deepEqual(problems, []);
  await context.close();
});

test("a signed-out visitor to a console page is sent to sign in", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${WEB}/cases`);
  await page.waitForURL("**/signin");
  await context.close();
});

test("an auditor can read but cannot run a drill", async () => {
  const { context, page } = await session("auditor");
  await page.goto(`${WEB}/simulate`);
  await page.getByRole("button", { name: "Run" }).first().waitFor();
  for (const button of await page.getByRole("button", { name: "Run" }).all()) assert.ok(await button.isDisabled());
  await context.close();
});

test("an analyst runs a scenario, watches it live, and finds the case file", async () => {
  const { context, page, problems } = await session("fraud_analyst");
  await page.goto(`${WEB}/simulate`);
  await page.getByRole("button", { name: "Run" }).first().click();
  const started = page.getByRole("heading", { name: /Started PS-/ });
  await started.waitFor({ timeout: 30000 });
  const caseId = (await started.innerText()).match(/PS-[\d-]+/)[0];
  await page.getByRole("link", { name: "Watch it live" }).click();
  await page.waitForURL(/\/live/);
  await page.locator("select").first().waitFor();
  await page.waitForFunction((id) => document.querySelector("select")?.value === id, caseId, { timeout: 20000 });
  await page.goto(`${WEB}/cases/${caseId}`);
  await page.getByText(caseId).first().waitFor();
  await page.goto(`${WEB}/cases`);
  await page.getByText(caseId).first().waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("a supervisor blocks a held payment from the holds page and the ledger agrees", async () => {
  const caseId = await trigger(token, "cus_dilani", "sim_swap_transfer");
  let held;
  for (let i = 0; i < 40; i++) {
    held = (await call(token, "GET", `/api/cases/${caseId}`)).json;
    if (held.transaction?.status === "held" && held.case.status.includes("waiting")) break;
    await new Promise((r) => setTimeout(r, 750));
  }
  const { context, page } = await session("bank_supervisor");
  await page.goto(`${WEB}/holds`);
  const row = page.locator("tr", { hasText: "Dilani" });
  await row.first().waitFor({ timeout: 20000 });
  await row.first().click();
  await page.getByLabel("Reason").fill("Customer could not be reached and the payee is new");
  await page.getByRole("button", { name: "Block", exact: true }).click();
  for (let i = 0; i < 30; i++) {
    const now = (await call(token, "GET", `/api/cases/${caseId}`)).json;
    if (now.transaction?.status === "blocked") return context.close();
    await new Promise((r) => setTimeout(r, 750));
  }
  assert.fail("the payment was not blocked");
});

test("the audit log records what the analyst did", async () => {
  const admin = await operatorToken("admin@payshield.lk", "pilot-admin");
  const log = await call(admin, "GET", "/api/admin/audit");
  assert.equal(log.status, 200);
  assert.ok(JSON.stringify(log.json).includes("sign_in"));
});
