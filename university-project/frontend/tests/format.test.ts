import assert from "node:assert/strict";
import test from "node:test";
import { chipFor, clock, colombo, humanizeResult, lkr, mmss, prettyPhone, statusLabel } from "../lib/format.ts";
import { markerTone } from "../lib/markers.ts";

test("money is shown with the rupee prefix and thousands separators, and never as NaN", () => {
  assert.equal(lkr(0), "Rs. 0");
  assert.equal(lkr(1245800), "Rs. 1,245,800");
  assert.equal(lkr(null), "Rs. 0");
  assert.equal(lkr(undefined), "Rs. 0");
  assert.equal(lkr(500000), "Rs. 500,000");
});

test("times are shown in Sri Lanka time and a bad value is blank, not an error", () => {
  assert.equal(colombo("2026-01-01T00:00:00Z"), "1 Jan, 05:30 SLT");
  assert.equal(colombo("2026-06-30T20:00:00Z"), "1 Jul, 01:30 SLT");
  assert.equal(colombo(""), "");
  assert.equal(colombo(null), "");
  assert.equal(colombo("not a date"), "");
  assert.equal(clock(new Date("2026-01-01T00:00:05Z")), "05:30:05");
});

test("phone numbers are grouped only when they are a full Sri Lankan number", () => {
  assert.equal(prettyPhone("+94772451183"), "+94 77 245 1183");
  assert.equal(prettyPhone("+9477245"), "+9477245");
  assert.equal(prettyPhone("0772451183"), "0772451183");
  assert.equal(prettyPhone(null), "");
});

test("every case and payment status has a plain label, and an unknown one is shown as it is", () => {
  const known = ["new", "running", "waiting_customer", "waiting_verification", "escalating", "escalated", "open", "closed_blocked", "closed_released", "closed_cleared", "error", "held", "blocked", "released", "pending", "hold", "block", "release"];
  for (const status of known) {
    const label = statusLabel(status);
    assert.ok(label && !label.includes("_"), status);
  }
  assert.equal(statusLabel("something_new"), "something_new");
  assert.equal(statusLabel(null), "");
  assert.equal(statusLabel(undefined), "");
});

test("a status colour is chosen for blocked, held, released and running, and the rest are neutral", () => {
  for (const v of ["high", "closed_blocked", "blocked", "block", "error"]) assert.equal(chipFor(v), "high");
  for (const v of ["medium", "waiting_customer", "held", "hold", "escalated"]) assert.equal(chipFor(v), "medium");
  for (const v of ["low", "closed_released", "released", "release"]) assert.equal(chipFor(v), "ok");
  for (const v of ["running", "new"]) assert.equal(chipFor(v), "info");
  for (const v of ["", null, undefined, "whatever"]) assert.equal(chipFor(v as string), "neutral");
});

test("raw tool results are turned into a sentence an analyst can read", () => {
  assert.equal(humanizeResult("checkSimSwap swapped=false, latestSimChange=none"), "No SIM swap in the checked window.");
  assert.match(humanizeResult("checkSimSwap swapped=true, latestSimChange=2026-01-01 00:00:00 UTC"), /^SIM was swapped\. Latest change 1 Jan, 05:30 SLT\.$/);
  assert.match(humanizeResult("checkSimSwap swapped=true, latestSimChange=none"), /unknown time/);
  assert.equal(humanizeResult("Held at 2026-01-01T00:00:00Z by the rules"), "Held at 1 Jan, 05:30 SLT by the rules");
  assert.equal(humanizeResult("nothing to change here"), "nothing to change here");
  assert.equal(humanizeResult(""), "");
  assert.equal(humanizeResult(null), "");
});

test("a countdown shows minutes and seconds and never goes negative", () => {
  assert.equal(mmss(0), "0:00");
  assert.equal(mmss(59_999), "0:59");
  assert.equal(mmss(180_000), "3:00");
  assert.equal(mmss(600_000), "10:00");
  assert.equal(mmss(-5000), "0:00");
});

test("a map marker is held, blocked, watched or normal, in that order of priority", () => {
  const base = { id: "c", held_lkr: 0, severity: "low", open_cases: 0 } as never;
  assert.equal(markerTone({ ...(base as object), held_lkr: 1 } as never), "held");
  assert.equal(markerTone({ ...(base as object), held_lkr: 1, severity: "high", open_cases: 2 } as never), "held");
  assert.equal(markerTone({ ...(base as object), severity: "high", open_cases: 1 } as never), "blocked");
  assert.equal(markerTone({ ...(base as object), severity: "high", open_cases: 0 } as never), "normal");
  assert.equal(markerTone({ ...(base as object), open_cases: 1 } as never), "watch");
  assert.equal(markerTone({ ...(base as object), severity: "medium" } as never), "watch");
  assert.equal(markerTone(base), "normal");
});
