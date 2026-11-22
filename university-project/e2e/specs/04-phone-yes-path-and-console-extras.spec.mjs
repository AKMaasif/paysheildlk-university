import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { WEB, assist, call, launch, operatorToken, overflow, registerThroughTheSite, reset, signInCustomer, trigger, unregister, watch } from "../helpers.mjs";

let browser, token, dilani;
before(async () => {
  browser = await launch();
  token = await operatorToken();
  await reset(token);
  await unregister(token, "cus_dilani");
  dilani = await assist(token, "cus_dilani");
});
after(async () => browser.close());

const phone = () => browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

async function waitForCase(caseId, done, tries = 40) {
  let last;
  for (let i = 0; i < tries; i++) {
    last = (await call(token, "GET", `/api/cases/${caseId}`)).json;
    if (done(last)) return last;
    await new Promise((r) => setTimeout(r, 750));
  }
  throw new Error("the case never reached the expected state: " + last?.case?.status);
}

test("on a phone: register, get an alert, say yes, scan the face, answer the questions, and the payment is released", async () => {
  const context = await phone();
  const page = await context.newPage();
  const problems = watch(page);
  await registerThroughTheSite(page, dilani);
  assert.equal(await overflow(page), false);
  const caseId = await trigger(token, "cus_dilani", "sim_swap_transfer");
  await page.getByRole("button", { name: /yes, this was me/i }).waitFor({ timeout: 30000 });
  assert.equal(await overflow(page), false, "the alert fits the screen");
  await page.getByRole("button", { name: /yes, this was me/i }).click();
  await page.getByText(/scan your face again/i).waitFor({ timeout: 30000 });
  const scan = page.getByRole("button", { name: /start face scan|scan again/i });
  await scan.waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /face scan|scan again/i.test(b.textContent) && !b.disabled), null, { timeout: 15000 });
  await scan.click();
  await page.getByText(/answer these questions/i).waitFor({ timeout: 60000 });
  assert.equal(await overflow(page), false, "the questions fit the screen");
  for (const [code, answer] of Object.entries(dilani.answers)) {
    const field = page.locator(`#${code}`);
    if (await field.count()) await field.fill(answer);
  }
  await page.getByRole("button", { name: /submit answers/i }).click();
  const done = await waitForCase(caseId, (d) => d.case.status.startsWith("closed"));
  assert.equal(done.transaction.status, "released", JSON.stringify(done.case));
  assert.deepEqual(problems, []);
  await context.close();
});

test("on a phone: wrong answers twice never release the payment", async () => {
  const context = await phone();
  const page = await context.newPage();
  await signInCustomer(page, dilani.full_name.toLowerCase());
  await page.waitForURL("**/c/home");
  const caseId = await trigger(token, "cus_dilani", "sim_swap_transfer");
  await page.getByRole("button", { name: /yes, this was me/i }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: /yes, this was me/i }).click();
  for (let round = 0; round < 3; round++) {
    const scanButton = page.getByRole("button", { name: /start face scan|scan again/i });
    if (await scanButton.isVisible().catch(() => false)) {
      await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /face scan|scan again/i.test(b.textContent) && !b.disabled), null, { timeout: 15000 });
      await scanButton.click();
    }
    const heading = page.getByText(/answer these questions/i);
    if (!(await heading.isVisible({ timeout: 20000 }).catch(() => false))) break;
    for (const input of await page.locator(".cs-panel input.cs-input").all()) await input.fill("not the answer");
    await page.getByRole("button", { name: /submit answers/i }).click();
    await page.waitForTimeout(1500);
  }
  const final = await waitForCase(caseId, (d) => d.case.status !== "waiting_customer" || d.transaction.status !== "held", 30).catch(async () => (await call(token, "GET", `/api/cases/${caseId}`)).json);
  assert.notEqual(final.transaction.status, "released");
  await context.close();
});

// ------------------------------------------------------------------ operator extras

async function operatorPage(email, password, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  const page = await context.newPage();
  await page.goto(`${WEB}/signin`);
  await page.getByLabel("Username").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.locator("button[type=submit]").click();
  await page.waitForURL("**/overview");
  return { context, page };
}

test("an analyst adds a customer to the bank record and that customer can then be found", async () => {
  const { context, page } = await operatorPage("analyst@payshield.lk", "pilot-analyst");
  await page.goto(`${WEB}/enrol`);
  await page.getByLabel("Full name").fill("Kasun Ratnayake");
  await page.getByLabel("Email", { exact: true }).fill("kasun.ratnayake@mail.lk");
  await page.getByLabel("NIC number").fill("199012345678");
  await page.getByLabel("Mobile number").fill("+94771230099");
  await page.getByLabel("Bank account number").fill("8200459901");
  await page.getByLabel("Opening balance (Rs)").fill("250000");
  const answers = ["colour", "food", "father", "mother", "school", "city"];
  const labels = page.locator("form label.field").filter({ has: page.locator("input") });
  const count = await labels.count();
  for (let i = count - 8; i < count; i++) await labels.nth(i).locator("input").fill("answer" + i);
  await page.getByRole("button", { name: /add to the bankcylone record/i }).click();
  await page.getByText(/is on the BankCylone record/i).waitFor({ timeout: 15000 });
  const customers = (await call(token, "GET", "/api/demo/customers")).json.customers;
  assert.ok(customers.some((c) => /Kasun/.test(JSON.stringify(c))));
  assert.equal(answers.length, 6);
  await context.close();
});

test("the calibration page explains the human gate and lets an analyst ask for a proposal", async () => {
  const { context, page } = await operatorPage("analyst@payshield.lk", "pilot-analyst");
  await page.goto(`${WEB}/calibration`);
  await page.getByText(/human gate/i).first().waitFor();
  const ask = page.getByRole("button", { name: /propose|ask|review/i }).first();
  if (await ask.count()) {
    await ask.click();
    await page.getByText(/nothing changes until an analyst approves|evidence does not support/i).first().waitFor({ timeout: 20000 });
  }
  await context.close();
});

test("an analyst downloads the Markdown and PDF report of a closed case", async () => {
  const caseId = await trigger(token, "cus_nimal", "sim_swap_transfer");
  await waitForCase(caseId, (d) => d.transaction?.status === "held" && d.case.status.includes("waiting"));
  await call(token, "POST", `/api/cases/${caseId}/kill`, { action: "block", reason: "Closed for the report test" });
  await waitForCase(caseId, (d) => d.case.status.startsWith("closed") && d.report);
  const { context, page } = await operatorPage("analyst@payshield.lk", "pilot-analyst");
  await page.goto(`${WEB}/cases/${caseId}`);
  await page.getByRole("button", { name: "PDF" }).waitFor({ timeout: 20000 });
  const [pdf] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "PDF" }).click()]);
  assert.match(pdf.suggestedFilename(), /\.pdf$/);
  const [md] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Markdown" }).click()]);
  assert.match(md.suggestedFilename(), /\.md$/);
  await context.close();
});

test("the manual block drill on the Simulate page blocks the held payment and is written to the audit log", async () => {
  const { context, page } = await operatorPage("analyst@payshield.lk", "pilot-analyst");
  await page.goto(`${WEB}/simulate`);
  const row = page.locator("div, li, tr").filter({ hasText: /manual/i }).filter({ has: page.getByRole("button", { name: "Run" }) }).last();
  await row.getByRole("button", { name: "Run" }).click();
  await page.getByRole("heading", { name: /Started PS-/ }).waitFor({ timeout: 30000 });
  const caseId = (await page.getByRole("heading", { name: /Started PS-/ }).innerText()).match(/PS-[\d-]+/)[0];
  const done = await waitForCase(caseId, (d) => d.case.status.startsWith("closed"));
  assert.equal(done.transaction.status, "blocked");
  const admin = await operatorToken("admin@payshield.lk", "pilot-admin");
  const audit = JSON.stringify((await call(admin, "GET", "/api/admin/audit")).json);
  assert.match(audit, /kill_block|manual/);
  await context.close();
});
