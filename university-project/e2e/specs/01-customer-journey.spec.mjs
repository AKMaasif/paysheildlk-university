import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { API, WEB, PASSWORD, assist, call, fillRegistration, launch, operatorToken, registerThroughTheSite, reset, signInCustomer, trigger, unregister, watch } from "../helpers.mjs";

let browser, token, rec;
before(async () => {
  browser = await launch();
  token = await operatorToken();
  await reset(token);
  await unregister(token, "cus_nimal");
  rec = await assist(token, "cus_nimal");
});
after(async () => browser.close());

const fresh = async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  return { context, page, problems: watch(page) };
};

test("the landing page tells the truth and links to both ways in", async () => {
  const { context, page, problems } = await fresh();
  await page.goto(`${WEB}/c`);
  assert.match(await page.title(), /PayShield|BankCylone/i);
  assert.ok(await page.getByRole("link", { name: /register/i }).first().isVisible());
  assert.ok(await page.getByRole("link", { name: /log in/i }).first().isVisible());
  assert.match(await page.locator("body").innerText(), /BankCylone/);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a signed-out visitor to the account page is sent to log in", async () => {
  const { context, page } = await fresh();
  await page.goto(`${WEB}/c/home`);
  await page.waitForURL("**/c/login");
  await context.close();
});

test("registration step one explains what is wrong with each field", async () => {
  const { context, page } = await fresh();
  await page.goto(`${WEB}/c/register`);
  const cases = [
    [{ full_name: "Nimal" }, /full name/i],
    [{ email: "nope" }, /email/i],
    [{ nic: "123" }, /NIC/],
    [{ phone: "12345" }, /mobile/i],
    [{ account_number: "ab" }, /account number/i],
  ];
  for (const [override, expected] of cases) {
    await fillRegistration(page, rec, override);
    await page.getByRole("button", { name: "Continue" }).click();
    assert.match(await page.locator(".cs-error").innerText(), expected, JSON.stringify(override));
  }
  await context.close();
});

test("details that do not match the bank record are refused without saying which one", async () => {
  const { context, page } = await fresh();
  await page.goto(`${WEB}/c/register`);
  await fillRegistration(page, rec, { account_number: "9999999999" });
  await page.getByRole("button", { name: "Continue" }).click();
  await page.fill("#p", PASSWORD);
  await page.fill("#c", PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /start face scan/i }).click();
  const alert = page.locator(".cs-error");
  await alert.waitFor();
  assert.match(await alert.innerText(), /do not match/i);
  assert.doesNotMatch(await alert.innerText(), /account number/i);
  await context.close();
});

test("the password rules and the re-entry check are enforced before the scan", async () => {
  const { context, page } = await fresh();
  await page.goto(`${WEB}/c/register`);
  await fillRegistration(page, rec);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.fill("#p", "short");
  await page.getByRole("button", { name: "Continue" }).click();
  assert.match(await page.locator(".cs-error").innerText(), /meets both rules/i);
  await page.fill("#p", PASSWORD);
  await page.fill("#c", PASSWORD + "x");
  assert.ok(await page.getByText(/not the same yet/i).isVisible());
  await page.getByRole("button", { name: "Continue" }).click();
  assert.match(await page.locator(".cs-error").innerText(), /not the same/i);
  await context.close();
});

test("a customer registers with a face scan, lands on their account and sees balance and history", async () => {
  const { context, page, problems } = await fresh();
  await registerThroughTheSite(page, rec);
  await page.getByRole("heading", { level: 1 }).waitFor();
  const text = await page.locator("body").innerText();
  assert.match(text, /Nimal/);
  assert.match(text, /BankCylone/);
  assert.match(text, /Rs\.?\s?[\d,]+/);
  assert.match(text, /Recent transactions/);
  assert.match(text, /\u2022{4,}\d{4}/, "account number is masked");
  assert.doesNotMatch(text, new RegExp(rec.account_number), "the full account number is not shown");
  assert.deepEqual(problems, []);
  await context.close();
});

test("a second registration for the same record is refused", async () => {
  const { context, page } = await fresh();
  await page.goto(`${WEB}/c/register`);
  await fillRegistration(page, rec);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.fill("#p", PASSWORD);
  await page.fill("#c", PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /start face scan/i }).click();
  await page.locator(".cs-error").waitFor();
  assert.match(await page.locator(".cs-error").innerText(), /already|registered/i);
  await context.close();
});

test("log in by username: wrong password is refused, right password opens the account, log out closes it", async () => {
  const { context, page } = await fresh();
  await signInCustomer(page, "nimal perera", "Wrong-0000");
  await page.locator(".cs-error").waitFor();
  assert.match(await page.locator(".cs-error").innerText(), /incorrect|invalid|wrong|not recognised/i);
  await signInCustomer(page, "Nimal  Perera", PASSWORD);
  await page.waitForURL("**/c/home");
  await page.getByRole("button", { name: /log out|sign out/i }).click();
  await page.waitForURL(/\/c(\/login)?$/);
  await page.goto(`${WEB}/c/home`);
  await page.waitForURL("**/c/login");
  await context.close();
});

test("a payment held after a SIM swap: the customer says it was not them, it is blocked and a receipt is offered", async () => {
  const { context, page } = await fresh();
  const caseId = await trigger(token, "cus_nimal", "sim_swap_transfer");
  await signInCustomer(page, "nimal perera");
  await page.waitForURL("**/c/home");
  await page.getByRole("button", { name: /no, this was not me/i }).waitFor({ timeout: 30000 });
  assert.match(await page.locator("body").innerText(), /on hold|held|Is this payment yours/i);
  await page.getByRole("button", { name: /no, this was not me/i }).click();
  await page.getByText(/blocked|stopped|safe/i).first().waitFor({ timeout: 30000 });
  let status;
  for (let i = 0; i < 30; i++) {
    status = await call(token, "GET", `/api/cases/${caseId}`);
    if (status.json.case.status.startsWith("closed")) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  assert.equal(status.json.case.status.startsWith("closed"), true, status.json.case.status);
  assert.match(JSON.stringify(status.json), /block/i);
  await page.getByRole("button", { name: /download your receipt/i }).waitFor({ timeout: 30000 });
  await context.close();
});

test("after the alert the customer is asked to change their password, old then new then repeated, and the new one works", async () => {
  const { context, page } = await fresh();
  await signInCustomer(page, "nimal perera");
  await page.waitForURL(/\/c\/(home|password)/);
  await page.goto(`${WEB}/c/password`);
  await page.fill("#o", "Not-the-password-1");
  await page.fill("#n", "Lantern-7391");
  await page.fill("#r", "Lantern-7391");
  await page.getByRole("button", { name: "Change password" }).click();
  await page.locator(".cs-error").waitFor();
  assert.match(await page.locator(".cs-error").innerText(), /current password|incorrect/i);
  await page.fill("#o", PASSWORD);
  await page.fill("#n", "Lantern-7391");
  await page.fill("#r", "Different-1234");
  await page.getByRole("button", { name: "Change password" }).click();
  await page.locator(".cs-error").waitFor();
  assert.match(await page.locator(".cs-error").innerText(), /not the same|match/i);
  await page.fill("#r", "Lantern-7391");
  await page.getByRole("button", { name: "Change password" }).click();
  await page.getByText(/has been changed/i).first().waitFor({ timeout: 15000 });
  const { context: c2, page: p2 } = await fresh();
  await signInCustomer(p2, "nimal perera", PASSWORD);
  await p2.locator(".cs-error").waitFor();
  await signInCustomer(p2, "nimal perera", "Lantern-7391");
  await p2.waitForURL("**/c/home");
  await context.close();
  await c2.close();
});

test("the customer's token cannot read the operator side", async () => {
  const { context, page } = await fresh();
  await signInCustomer(page, "nimal perera", "Lantern-7391");
  await page.waitForURL("**/c/home");
  const customerToken = await page.evaluate(() => localStorage.getItem("payshield_customer"));
  for (const path of ["/api/overview", "/api/cases", "/api/analytics", "/api/security/status", "/api/agents"]) {
    const reply = await fetch(API + path, { headers: { Authorization: `Bearer ${customerToken}` } });
    assert.equal(reply.status, 403, path);
  }
  await context.close();
});

test("a customer who has not registered cannot sign in", async () => {
  const { context, page } = await fresh();
  await signInCustomer(page, "dilani wickramasinghe", PASSWORD);
  await page.locator(".cs-error").waitFor();
  await context.close();
});
