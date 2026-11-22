import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { WEB, launch, operatorToken, reset, watch } from "../helpers.mjs";

const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const PAGES = ["overview", "cases", "holds", "live", "map", "analytics", "agents", "reports", "calibration", "simulate", "demo", "sim-events", "security", "settings", "enrol", "admin"];

let browser, page, problems;
before(async () => {
  browser = await launch();
  await reset(await operatorToken());
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  problems = watch(page);
  page.on("console", (m) => { if (m.type() === "error") problems.push("console: " + m.text().slice(0, 300)); });
  await page.goto(`${WEB}/signin`);
  await page.getByLabel("Username").fill("admin@payshield.lk");
  await page.getByLabel("Password").fill("pilot-admin");
  await page.locator("button[type=submit]").click();
  await page.waitForURL("**/overview");
});
after(async () => browser.close());

for (const name of PAGES) {
  test(`/${name} has no serious or critical accessibility violation`, async () => {
    problems.length = 0;
    await page.goto(`${WEB}/${name}`);
    await page.locator("h1").first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(1500);
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } }));
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    assert.deepEqual(problems.filter((p) => !/tile\.openstreetmap|ERR_|Failed to load resource/.test(p)), [], "the page raised errors");
    assert.deepEqual(bad.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`), []);
  });
}
