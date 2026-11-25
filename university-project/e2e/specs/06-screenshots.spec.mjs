// Refreshes the screenshots of the customer site in docs/customer-site from the real running site.
// It only runs when E2E_SCREENSHOTS names the folder to write into, for example:
//   E2E_SCREENSHOTS=../docs/customer-site node run.mjs specs/06-screenshots.spec.mjs
// In an ordinary test run it registers nothing and does nothing.
import test from "node:test";
import path from "node:path";
import { WEB, PASSWORD, assist, call, enterCode, fillRegistration, launch, operatorToken, reset, trigger, unregister, usernameFor } from "../helpers.mjs";

const dir = process.env.E2E_SCREENSHOTS ? path.resolve(process.env.E2E_SCREENSHOTS) : "";

if (dir) {
  test("customer site screenshots", { timeout: 600000 }, async () => {
    const browser = await launch();
    const token = await operatorToken();
    await reset(token);
    await unregister(token, "cus_nimal");
    const rec = await assist(token, "cus_nimal");
    const shot = (page, name, options = {}) => page.screenshot({ path: path.join(dir, name), ...options });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();

    // landing
    await page.goto(`${WEB}/c`);
    await page.waitForLoadState("networkidle");
    await shot(page, "01-landing-full.png", { fullPage: true });
    await shot(page, "01b-landing-top.png");

    // registration: a mismatch first, then the real details
    await page.goto(`${WEB}/c/register`);
    await fillRegistration(page, rec, { nic: "199999999999" });
    await page.getByRole("button", { name: "Continue" }).click();
    await page.fill("#p", PASSWORD);
    await page.fill("#c", PASSWORD);
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: /start face scan/i }).click();
    await page.locator(".cs-error").waitFor({ timeout: 60000 });
    await shot(page, "06-register-mismatch-error.png");
    await page.goto(`${WEB}/c/register`);
    await fillRegistration(page, rec);
    await shot(page, "02-register-details.png");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.fill("#u", usernameFor(rec.full_name));
    await page.fill("#p", PASSWORD);
    await page.fill("#c", PASSWORD);
    await shot(page, "05-register-password.png");
    await page.getByRole("button", { name: "Continue" }).click();
    await shot(page, "03-register-face-scan.png");
    await page.getByRole("button", { name: /start face scan/i }).click();
    await page.waitForSelector("#code", { timeout: 60000 });
    await page.waitForSelector(".cs-qr img", { timeout: 20000 });
    await shot(page, "17-register-authenticator.png");
    await enterCode(page, rec.authenticator.secret);
    await page.waitForURL("**/c/home", { timeout: 60000 });
    await page.waitForFunction(() => !document.body.innerText.includes("Opening your account"), null, { timeout: 30000 });
    await page.waitForLoadState("networkidle");
    await shot(page, "07-home-account.png", { fullPage: true });

    // a held payment: the question, the selfie, the security questions, then a second one answered "no"
    await trigger(token, "cus_nimal", "sim_swap_transfer");
    await page.getByRole("button", { name: /yes, this was me/i }).waitFor({ timeout: 60000 });
    await shot(page, "08-home-payment-question.png", { fullPage: true });
    await page.getByRole("button", { name: /yes, this was me/i }).click();
    const scan = page.getByRole("button", { name: /start face scan|scan again/i });
    await scan.waitFor({ timeout: 30000 });
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /face scan|scan again/i.test(b.textContent) && !b.disabled), null, { timeout: 15000 });
    await shot(page, "13-home-selfie.png", { fullPage: true });
    await scan.click();
    await page.getByText(/answer these questions/i).waitFor({ timeout: 60000 });
    for (const [code, answer] of Object.entries(rec.answers)) {
      const field = page.locator(`#${code}`);
      if (await field.count()) await field.fill(answer);
    }
    await shot(page, "14-home-questions.png", { fullPage: true });
    await page.getByRole("button", { name: /submit answers/i }).click();
    await page.waitForTimeout(3000);
    await trigger(token, "cus_nimal", "sim_swap_transfer");
    await page.getByRole("button", { name: /no, this was not me/i }).waitFor({ timeout: 60000 });
    await page.getByRole("button", { name: /no, this was not me/i }).click();
    await page.getByText(/blocked|stopped|safe/i).first().waitFor({ timeout: 30000 });
    await shot(page, "11-home-after-block.png", { fullPage: true });

    // password change
    await page.goto(`${WEB}/c/password`);
    await shot(page, "09-password-required.png");
    await page.fill("#o", PASSWORD);
    await page.fill("#n", "Lantern-7391");
    await page.fill("#r", "Lantern-7391");
    await page.getByRole("button", { name: "Change password" }).click();
    await page.getByText(/has been changed/i).first().waitFor({ timeout: 15000 });
    await shot(page, "10-password-changed.png");
    await context.close();

    // log in: a wrong password, then the code screen
    const login = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const lp = await login.newPage();
    await lp.goto(`${WEB}/c/login`);
    await lp.fill("#username", usernameFor(rec.full_name));
    await lp.fill("#password", "Not-the-password-1");
    await lp.getByRole("button", { name: "Log in" }).click();
    await lp.locator(".cs-error").waitFor({ timeout: 15000 });
    await shot(lp, "12-login-wrong-password.png");
    await lp.fill("#password", "Lantern-7391");
    await lp.getByRole("button", { name: "Log in" }).click();
    await lp.waitForSelector("#code", { timeout: 15000 });
    await shot(lp, "18-login-code.png");
    await login.close();

    // a phone
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    const pp = await phone.newPage();
    await pp.goto(`${WEB}/c`);
    await pp.waitForLoadState("networkidle");
    await shot(pp, "15-phone-landing.png", { fullPage: true });
    await pp.goto(`${WEB}/c/register`);
    await shot(pp, "16-phone-register.png", { fullPage: true });
    await phone.close();
    await call(token, "POST", "/api/demo/reset");
    await browser.close();
  });
}
