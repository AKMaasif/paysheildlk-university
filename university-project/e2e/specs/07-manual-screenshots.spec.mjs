// Takes the console screenshots used in the user manual (docs/user-manual/images).
// Only runs when E2E_MANUAL_SHOTS names the folder to write into, for example:
//   E2E_MANUAL_SHOTS=../docs/user-manual/images node run.mjs specs/07-manual-screenshots.spec.mjs
import test from "node:test";
import path from "node:path";
import { WEB, launch, operatorToken, reset, trigger } from "../helpers.mjs";

const dir = process.env.E2E_MANUAL_SHOTS ? path.resolve(process.env.E2E_MANUAL_SHOTS) : "";

const PAGES = [
  ["overview", "console-overview.png"],
  ["cases", "console-cases.png"],
  ["holds", "console-on-hold.png"],
  ["reports", "console-reports.png"],
  ["analytics", "console-analytics.png"],
  ["map", "console-map.png"],
  ["sim-events", "console-sim-events.png"],
  ["agents", "console-agents-models.png"],
  ["simulate", "console-simulate.png"],
  ["calibration", "console-calibration.png"],
  ["demo", "console-demo-controls.png"],
  ["enrol", "console-add-customer.png"],
  ["security", "console-security.png"],
  ["settings", "console-settings.png"],
];

if (dir) {
  test("console screenshots for the manual", { timeout: 900000 }, async () => {
    const browser = await launch();
    const token = await operatorToken();
    await reset(token);
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await page.goto(`${WEB}/signin`);
    await page.getByLabel("Username").fill("analyst@payshield.lk");
    await page.getByLabel("Password").fill("pilot-analyst");
    await page.locator("button[type=submit]").click();
    await page.waitForURL("**/overview", { timeout: 30000 });

    // The live page is opened first, as an analyst would have it, so the steps arrive as they happen.
    const fit = async () => {
      await page.locator(".react-flow__controls-fitview").click().catch(() => undefined);
      await page.waitForTimeout(1200);
    };
    await page.goto(`${WEB}/live`);
    await page.waitForLoadState("networkidle");
    const closed = await trigger(token, "cus_kumari", "port_out_critical"); // blocked at once, so there is also a report
    await page.waitForTimeout(15000);
    await page.selectOption("select", closed).catch(() => undefined);
    await fit();
    await page.screenshot({ path: path.join(dir, "console-live-activity.png") });
    const waiting = await trigger(token, "cus_nimal", "sim_swap_transfer"); // waits for the customer
    await page.waitForTimeout(20000);
    await page.selectOption("select", waiting).catch(() => undefined);
    await fit();
    await page.screenshot({ path: path.join(dir, "console-live-waiting.png") });
    for (const [route, file] of PAGES) {
      await page.goto(`${WEB}/${route}`);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(route.startsWith("live") ? 9000 : 2500);
      if (route.startsWith("live")) {
        await page.locator(".react-flow__controls-fitview").click().catch(() => undefined); // fit the whole flow in view
        await page.waitForTimeout(1000);
      }
      await page.screenshot({ path: path.join(dir, file) });
    }
    await browser.close();
  });
}
