import assert from "node:assert/strict";
import test from "node:test";
import { deliveryLine, freezeLine } from "../lib/delivery.ts";

const d = (state: string, extra: Record<string, unknown> = {}, attempts?: number) => ({ delivery: { state, ...extra }, attempts });

test("a logged message is never described as sent", () => {
  const line = deliveryLine("sms", d("logged_only"));
  assert.match(line.text, /LOGGED ONLY/);
  assert.doesNotMatch(line.text, /accepted|delivered|sent by/i);
  assert.equal(line.failed, false);
});

test("a message kept in the inbox says no provider was asked", () => {
  assert.match(deliveryLine("push", d("inbox_stored")).text, /INBOX ONLY, no provider was asked/);
});

test("a provider acceptance is shown as accepted and unconfirmed, with the provider's own id", () => {
  const line = deliveryLine("sms", d("provider_accepted", { provider: "textlk", provider_message_id: "abc123" }));
  assert.equal(line.text, "sms=ACCEPTED by textlk id=abc123, delivery UNCONFIRMED");
});

test("only a provider confirmation is shown as delivered", () => {
  assert.match(deliveryLine("email", d("delivered", { provider: "smtp" })).text, /DELIVERED, confirmed by smtp/);
  assert.doesNotMatch(deliveryLine("email", d("provider_accepted", { provider: "smtp" })).text, /DELIVERED/);
});

test("a failure names the reason and the number of attempts and is marked failed", () => {
  const line = deliveryLine("email", d("failed", { reason: "timeout" }, 2));
  assert.equal(line.text, "email=FAILED (timed out) after 2 attempts");
  assert.equal(line.failed, true);
  assert.match(deliveryLine("sms", d("failed", { reason: "rejected_credentials" })).text, /credentials refused/);
  assert.match(deliveryLine("sms", d("failed", { reason: "something_new" })).text, /something_new/);
});

test("a recipient that is not cleared for real sending, and the hourly limit, are shown as not sent", () => {
  assert.match(deliveryLine("sms", d("blocked_not_allowlisted")).text, /NOT SENT, recipient is not on the allow-list/);
  assert.match(deliveryLine("email", d("blocked_budget")).text, /NOT SENT, hourly limit/);
  assert.equal(deliveryLine("sms", d("blocked_budget")).failed, false);
});

test("an old event with no delivery record says so instead of guessing", () => {
  assert.equal(deliveryLine("sms", null).text, "sms=status not recorded");
  assert.equal(deliveryLine("sms", {}).text, "sms=status not recorded");
  assert.match(deliveryLine("sms", d("something_unexpected")).text, /something_unexpected/);
});

test("a SIM freeze is described as a recorded request unless the operator was really contacted", () => {
  assert.match(freezeLine({ simulated: true, operator_contacted: false }), /RECORDED in PayShield only \(simulated\), operator not contacted/);
  assert.match(freezeLine(null), /operator not contacted/);
  assert.match(freezeLine({ simulated: false, operator_contacted: true }), /REQUESTED from the operator/);
});
