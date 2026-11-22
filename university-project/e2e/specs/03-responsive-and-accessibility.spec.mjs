import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { WEB, assist, launch, operatorToken, overflow, registerThroughTheSite, reset, unregister, signInCustomer } from "../helpers.mjs";

const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const WIDTHS = [["phone", 390, 844], ["tablet", 768, 1024], ["desktop", 1440, 900]];
const PUBLIC = ["/c", "/c/login", "/c/register", "/signin"];

let browser, token, rec;
before(async () => {
  browser = await launch();
  token = await operatorToken();
  await reset(token);
  await unregister(token, "cus_nimal");
  rec = await assist(token, "cus_nimal");
  // One registered customer for the signed-in pages.
  const context = await browser.newContext();
  await registerThroughTheSite(await context.newPage(), rec);
  await context.close();
});
after(async () => browser.close());

async function open(width, height, path, signedIn = false) {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: width < 800 });
  const page = await context.newPage();
  if (signedIn) {
    await signInCustomer(page, "nimal perera");
    await page.waitForURL("**/c/home");
  }
  await page.goto(`${WEB}${path}`);
  await page.locator("h1:visible").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
  return { context, page };
}

async function axe(page) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(() => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
  return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

for (const [name, width, height] of WIDTHS) {
  for (const path of [...PUBLIC, "/c/home", "/c/password"]) {
    test(`${path} has no sideways scroll at ${name} width`, async () => {
      const { context, page } = await open(width, height, path, path.startsWith("/c/") && !["/c/login", "/c/register"].includes(path));
      assert.equal(await overflow(page), false);
      await context.close();
    });
  }
}

test("the main buttons are large enough to tap on a phone", async () => {
  for (const path of ["/c/login", "/c/register"]) {
    const { context, page } = await open(390, 844, path);
    for (const button of await page.locator("main button:visible, main a.cs-btn:visible").all()) {
      const box = await button.boundingBox();
      assert.ok(box && box.height >= 40, `${path}: "${(await button.innerText()).trim()}" is only ${box?.height}px tall`);
    }
    await context.close();
  }
});

for (const path of ["/c", "/c/login", "/c/register", "/c/home", "/c/password", "/signin"]) {
  test(`${path} has no serious or critical accessibility violation`, async () => {
    const { context, page } = await open(1280, 900, path, ["/c/home", "/c/password"].includes(path));
    const bad = await axe(page);
    assert.deepEqual(bad.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(", ")}`), []);
    await context.close();
  });
}

test("registration can be completed with the keyboard alone", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${WEB}/c/register`);
  await page.locator("#n").focus();
  await page.keyboard.type("Someone Else");
  await page.keyboard.press("Tab");
  await page.keyboard.type("someone@mail.lk");
  await page.keyboard.press("Tab");
  await page.keyboard.type("198524701999");
  await page.keyboard.press("Tab");
  await page.keyboard.type("0771111111");
  await page.keyboard.press("Tab");
  await page.keyboard.type("8200451190");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await page.locator("#p").waitFor();
  assert.ok(await page.locator("#p").isVisible());
  await context.close();
});

test("every field has a visible label and the error messages are announced", async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${WEB}/c/register`);
  for (const id of ["n", "e", "i", "m", "a"]) assert.ok((await page.locator(`label[for=${id}]`).count()) === 1, id);
  await page.getByRole("button", { name: "Continue" }).click();
  assert.equal(await page.locator(".cs-error").getAttribute("role"), "alert");
  await context.close();
});
