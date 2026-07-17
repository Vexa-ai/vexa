// Unit tests for merge-card-gate.mjs: the value-fsm verdict + wait logic (issue #655) and the
// acceptance row over closing-issue bodies (issue #712). Run: node --test scripts/merge-card-gate.test.mjs
//
// #655 regression: a non-terminal value-fsm run (queued|in_progress, conclusion === null) on the
// head sha was collapsed to "failure", red-carding a PR whose value-fsm was on its way to green.
// The fix makes the verdict four-state and WAITS for a terminal read before red-carding.
//
// #712 regression: PR #623's `Closes #622` auto-closed #622 with its live acceptance leg (a plain
// bullet, no ✅) undelivered — the acceptance row must catch both the checkbox and bullet shapes.

import test from "node:test";
import assert from "node:assert/strict";
import {
  verdictFromRuns,
  waitForTerminalValueFsm,
  openAcceptanceLegs,
  acceptanceFromIssues,
} from "./merge-card-gate.mjs";

const run = (o) => ({ name: "value-fsm", started_at: "2026-07-16T20:07:14Z", ...o });

// ── verdictFromRuns — pure, fixture-driven ──────────────────────────────────────────────────────

test("in_progress run (conclusion null) → pending, NOT failure (the #655 bug)", () => {
  assert.equal(verdictFromRuns([run({ status: "in_progress", conclusion: null })]), "pending");
});

test("queued run → pending", () => {
  assert.equal(verdictFromRuns([run({ status: "queued", conclusion: null })]), "pending");
});

test("completed + success → success", () => {
  assert.equal(verdictFromRuns([run({ status: "completed", conclusion: "success" })]), "success");
});

test("completed + failure → failure", () => {
  assert.equal(verdictFromRuns([run({ status: "completed", conclusion: "failure" })]), "failure");
});

test("completed + cancelled → failure (terminal non-success red-cards)", () => {
  assert.equal(verdictFromRuns([run({ status: "completed", conclusion: "cancelled" })]), "failure");
});

test("no value-fsm run → absent", () => {
  assert.equal(verdictFromRuns([{ name: "gates", status: "completed", conclusion: "success" }]), "absent");
});

test("newest run wins: a fresh in_progress re-run supersedes an old success → pending", () => {
  assert.equal(
    verdictFromRuns([
      run({ started_at: "2026-07-16T19:00:00Z", status: "completed", conclusion: "success" }),
      run({ started_at: "2026-07-16T20:07:14Z", status: "in_progress", conclusion: null }),
    ]),
    "pending",
  );
});

// ── waitForTerminalValueFsm — injected read/sleep, no network or real clock ──────────────────────

test("A1: in_progress → wait → success (poll settles to the real verdict)", async () => {
  const seq = [
    [run({ status: "in_progress", conclusion: null })],
    [run({ status: "in_progress", conclusion: null })],
    [run({ status: "completed", conclusion: "success" })],
  ];
  let i = 0, sleeps = 0;
  const verdict = await waitForTerminalValueFsm("sha", {
    read: () => seq[Math.min(i++, seq.length - 1)],
    wait: async () => { sleeps++; },
    attempts: 5, delayMs: 0,
  });
  assert.equal(verdict, "success");
  assert.equal(sleeps, 2, "backed off twice before the terminal read");
});

test("A3: terminal failure fails immediately, no wasted polling", async () => {
  let reads = 0;
  const verdict = await waitForTerminalValueFsm("sha", {
    read: () => { reads++; return [run({ status: "completed", conclusion: "failure" })]; },
    wait: async () => { throw new Error("should not sleep on a terminal read"); },
    attempts: 5, delayMs: 0,
  });
  assert.equal(verdict, "failure");
  assert.equal(reads, 1);
});

test("missing run: never registers → stays pending/absent, fails loudly (not silently green)", async () => {
  const verdict = await waitForTerminalValueFsm("sha", {
    read: () => [], // value-fsm never appears
    wait: async () => {},
    attempts: 3, delayMs: 0,
  });
  assert.notEqual(verdict, "success"); // the invariant: success must be positively observed
  assert.equal(verdict, "absent");
});

test("stuck in_progress → timeout → pending (caller red-cards, not green)", async () => {
  const verdict = await waitForTerminalValueFsm("sha", {
    read: () => [run({ status: "in_progress", conclusion: null })],
    wait: async () => {},
    attempts: 4, delayMs: 0,
  });
  assert.equal(verdict, "pending");
  assert.notEqual(verdict, "success");
});

// ── the ACCEPTANCE row — pure over closing-issue bodies, both house shapes (#712) ───────────────

test("RED, checkbox shape: one ticked + one unchecked leg → not mergeable, row names issue + count", () => {
  const body = [
    "## Value",
    "> the value line",
    "## Acceptance",
    "- [x] offline unit leg — delivered",
    "- [ ] live operator run",
  ].join("\n");
  assert.equal(openAcceptanceLegs(body), 1);
  const row = acceptanceFromIssues([{ number: 900, body }]);
  assert.equal(row.ok, false); // card verdict: not mergeable
  assert.match(row.why, /issue #900 has 1 undelivered acceptance leg\(s\)/);
  assert.match(row.why, /re-link as Part of #900/);
});

// The historical control: #622's ACTUAL acceptance section (fetched 2026-07-17) — bullets, not
// checkboxes. One leg carries a trailing ✅ (delivered), the live-operator leg is a plain bullet.
// A naive `- [ ]` count scores this "0 unchecked" and lets `Closes #622` auto-close it — exactly
// how PR #623 silently dropped the live leg (re-filed as #710). The guard must catch it.
const ISSUE_622_BODY = [
  "## How it runs",
  "Operator-run behind `VEXA_TX_KEY` (hits the paid hosted STT; nondeterministic) — NOT a CI gate. Re-run on STT-model bump; the generated `.txt` output is committed and pinned by `hallucination-filter.test.ts`.",
  "",
  "## Acceptance",
  "- Offline: the pure core (language set, non-speech corpus incl. RMS-0 silence, sweep with dedup + per-language failure isolation, provenance-headed sorted output) is unit-tested with a fake transcribe. ✅",
  "- Live (operator): a real run produces `<lang>.harvested.txt` for the languages that hallucinate; the reporter's #613 ja/tr phrases appear among them.",
  "",
  "Complements #619's near-silent RMS gate (gate stops silence at source; harvested list catches non-silent noise that passes the gate but still hallucinates) and supersedes the hand lists as the phrase SOURCE.",
].join("\n");

test("RED, historical control (#622's real bullet shape): plain bullet without ✅ is an open leg", () => {
  assert.equal(openAcceptanceLegs(ISSUE_622_BODY), 1); // the live-operator leg #623 dropped
  const row = acceptanceFromIssues([{ number: 622, body: ISSUE_622_BODY }]);
  assert.equal(row.ok, false); // the incident that motivated the row is demonstrably caught
  assert.match(row.why, /issue #622 has 1 undelivered acceptance leg\(s\)/);
});

test("GREEN: all legs delivered (ticked boxes / ✅ bullets) → row ✅; no closing ref (Part of) → row absent", () => {
  const boxes = "## Acceptance\n- [x] leg one\n- [x] leg two";
  const bullets = "## Acceptance\n- leg one ✅\n- leg two ✅";
  assert.equal(openAcceptanceLegs(boxes), 0);
  assert.equal(openAcceptanceLegs(bullets), 0);
  const row = acceptanceFromIssues([{ number: 901, body: boxes }, { number: 902, body: bullets }]);
  assert.equal(row.ok, true); // mergeable
  assert.match(row.why, /#901, #902/);
  // `Part of #N` is a plain reference — closingIssuesReferences is empty, the row does not exist.
  assert.equal(acceptanceFromIssues([]), null);
});

test("precision control: an unchecked box OUTSIDE the Acceptance section does not trip the row", () => {
  const body = [
    "## Plan",
    "- [ ] refactor the parser", // a plan checklist, not an acceptance leg
    "## Acceptance",
    "- [x] the one real leg",
    "## Refs",
    "- [ ] not acceptance either (section ended at the same-level heading)",
  ].join("\n");
  assert.equal(openAcceptanceLegs(body), 0);
  assert.equal(acceptanceFromIssues([{ number: 903, body }]).ok, true);
});
