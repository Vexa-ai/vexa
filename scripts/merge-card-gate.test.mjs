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
import { readFileSync } from "node:fs";
import {
  verdictFromRuns,
  waitForTerminalValueFsm,
  openAcceptanceLegs,
  acceptanceFromIssues,
  humanTestFromComments,
  humanTestSafely,
  humanTestConfig,
  cardOk,
  NO_HUMAN_TEST,
  renderCard,
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

// ── the HUMAN TEST row — pure over issue comments (#1687) ────────────────────────────────────

const humanTestFixture = JSON.parse(readFileSync(new URL("./fixtures/merge-card-human-test.json", import.meta.url), "utf8"));
const noHumanTest = `${NO_HUMAN_TEST} (informational — this row does not block merge)`;

test("human test qualifying: exactly one fixture comment is a non-author human validation", () => {
  const row = humanTestFromComments(humanTestFixture.comments, { author: "pr-author" });
  assert.equal(row.ok, true);
  assert.deepEqual(row.validations, [{
    by: "octo-validator",
    shape: "lite",
    ran: "joined a 20-minute Teams call and left the bot alone for 12",
    result: "bot exited left_alone at 6m02s as claimed",
    url: humanTestFixture.comments[1].html_url,
  }]);
  assert.equal(row.why, `@octo-validator (lite) — joined a 20-minute Teams call and left the bot alone for 12, bot exited left_alone at 6m02s as claimed ([comment](${humanTestFixture.comments[1].html_url}))`);
});

test("human test author-self: author comments are excluded case-insensitively", () => {
  const row = humanTestFromComments(humanTestFixture.comments, { author: "PR-AUTHOR" });
  assert.equal(row.validations.some((v) => v.by === "pr-author"), false);
  assert.equal(humanTestFromComments([humanTestFixture.comments[2]], { author: "pr-author" }).ok, false);
});

test("human test bot: both Bot type and bot login suffix exclude otherwise valid comments", () => {
  const row = humanTestFromComments(humanTestFixture.comments, { author: "pr-author" });
  assert.equal(row.validations.some((v) => v.by === "vexa-ci[bot]"), false);
  const body = humanTestFixture.comments[3].body;
  assert.equal(humanTestFromComments([{ body, user: { login: "someone[bot]", type: "User" } }]).ok, false);
  assert.equal(humanTestFromComments([{ body, user: { login: "someone[BOT]", type: "User" } }]).ok, false);
  assert.equal(humanTestFromComments([{ body, user: { login: "automation", type: "Bot" } }]).ok, false);
});

test("human test malformed: two parts, hosted, and a buried validation line are excluded", () => {
  for (const login of ["sloppy-validator", "wrong-shape", "buried-line"]) {
    const comment = humanTestFixture.comments.find((c) => c.user.login === login);
    assert.deepEqual(humanTestFromComments([comment]), { ok: false, why: noHumanTest, validations: [] });
  }
});

test("human test empty / defensive: missing comments or users return the exact informational reason", () => {
  for (const comments of [[], null, undefined, [{ body: humanTestFixture.comments[1].body, user: null }], [{ body: humanTestFixture.comments[1].body }]]) {
    assert.deepEqual(humanTestFromComments(comments), { ok: false, why: noHumanTest, validations: [] });
  }
});

test("human test agentLogins: configured agents are excluded case-insensitively", () => {
  assert.deepEqual(humanTestFromComments(humanTestFixture.comments, {
    author: "pr-author", agentLogins: ["Octo-Validator"],
  }), { ok: false, why: noHumanTest, validations: [] });
});

test("human test preserves order, accepts all three shapes, and omits absent comment links", () => {
  const row = humanTestFromComments([
    { body: "\n \n  vAlIdAtEd On COMPOSE, smoke test, passed  \nMore details", user: { login: "first" } },
    { body: "Validated on helm, upgrade, passed", user: { login: "second" }, html_url: "https://example.test/comment" },
    { body: "Validated on lite, join, passed", user: { login: "third" } },
  ]);
  assert.deepEqual(row.validations.map(({ by, shape, ran, result }) => ({ by, shape, ran, result })), [
    { by: "first", shape: "compose", ran: "smoke test", result: "passed" },
    { by: "second", shape: "helm", ran: "upgrade", result: "passed" },
    { by: "third", shape: "lite", ran: "join", result: "passed" },
  ]);
  assert.equal(row.ok, true);
  assert.equal(row.why, "@first (compose) — smoke test, passed; @second (helm) — upgrade, passed ([comment](https://example.test/comment)); @third (lite) — join, passed");
});

test("human test renderer: row follows Acceptance or Diff and preserves the marker and supplied verdict", () => {
  for (const acceptance of [null, { ok: true, why: "delivered" }]) {
    const rendered = renderCard({
      num: 4242, ok: true, valueOk: true, valueWhy: "fixture", diffOk: true, diffWhy: "fixture",
      acceptance, humanTest: humanTestFromComments([]),
    });
    assert.ok(rendered.startsWith("<!-- merge-card -->\n"));
    assert.match(rendered, /\| \*\*Human test\*\* \| ❌ \| no human test/);
    const lines = rendered.split("\n");
    const index = lines.findIndex((line) => line.startsWith("| **Human test**"));
    assert.ok(lines[index - 1].startsWith(`| **${acceptance ? "Acceptance" : "Diff"}**`));
    assert.match(rendered, /\*\*Ready to merge\*\*/);
  }
});

test("human test read is fail-soft: an unreadable comments list is an unavailable row, not a thrown card", () => {
  const row = humanTestSafely(() => { throw new Error("gh api: 403"); }, 4242, { author: "pr-author" });
  assert.equal(row.ok, false);
  assert.deepEqual(row.validations, []);
  assert.match(row.why, /could not read this PR's comments \(gh api: 403\) — the human-test row is unavailable/);
  // and the happy path still delegates to the parser
  assert.equal(humanTestSafely(() => humanTestFixture.comments, 4242, { author: "pr-author" }).validations[0].by, "octo-validator");
});

test("human test result is rendered, so a first-comma split never changes what a reader sees", () => {
  // `what I ran` contains a comma: the lazy split puts the tail in `result`, and the row joins
  // both back with the comma — the rendered text is the validator's own sentence either way.
  const row = humanTestFromComments([{
    body: "Validated on compose, ran POST /meetings, then GET /meetings, all 200s",
    user: { login: "alice" },
  }]);
  assert.deepEqual([row.validations[0].ran, row.validations[0].result], ["ran POST /meetings", "then GET /meetings, all 200s"]);
  assert.equal(row.why, "@alice (compose) — ran POST /meetings, then GET /meetings, all 200s");
});

test("the ❌ text tells the truth about blocking in BOTH flag states", () => {
  assert.equal(humanTestFromComments([], { blocking: false }).why, `${NO_HUMAN_TEST} (informational — this row does not block merge)`);
  assert.equal(humanTestFromComments([], { blocking: true }).why, `${NO_HUMAN_TEST} (required — this row blocks merge)`);
});

test("humanTestConfig: the flag is off unless explicitly turned on, and agent logins parse", () => {
  for (const env of [{}, { MERGE_CARD_REQUIRE_HUMAN_TEST: "" }, { MERGE_CARD_REQUIRE_HUMAN_TEST: "false" }, { MERGE_CARD_REQUIRE_HUMAN_TEST: "0" }, { MERGE_CARD_REQUIRE_HUMAN_TEST: "maybe" }])
    assert.equal(humanTestConfig(env).blocking, false, JSON.stringify(env));
  for (const v of ["1", "true", "TRUE", "yes", "Yes"])
    assert.equal(humanTestConfig({ MERGE_CARD_REQUIRE_HUMAN_TEST: v }).blocking, true, v);
  assert.deepEqual(humanTestConfig({}).agentLogins, []);
  assert.deepEqual(humanTestConfig({ MERGE_CARD_AGENT_LOGINS: " a , ,b " }).agentLogins, ["a", "b"]);
});

// THE invariant of #1687: while the flag is off, the human-test row cannot change the verdict.
test("cardOk: with the flag off a ❌ human test never moves the verdict; with it on, it does", () => {
  const rows = { valueOk: true, diffOk: true, acceptance: null };
  const red = humanTestFromComments([]);
  const green = humanTestFromComments(humanTestFixture.comments, { author: "pr-author" });
  assert.equal(cardOk({ ...rows, humanTest: red }), true);                    // default: not blocking
  assert.equal(cardOk({ ...rows, humanTest: red, blocking: false }), true);
  assert.equal(cardOk({ ...rows, humanTest: green, blocking: false }), true);
  assert.equal(cardOk({ ...rows, humanTest: red, blocking: true }), false);   // the flip
  assert.equal(cardOk({ ...rows, humanTest: green, blocking: true }), true);
  // and it never rescues a card the gating rows already failed
  assert.equal(cardOk({ valueOk: false, diffOk: true, acceptance: null, humanTest: green }), false);
  assert.equal(cardOk({ valueOk: true, diffOk: false, acceptance: null, humanTest: green }), false);
  assert.equal(cardOk({ valueOk: true, diffOk: true, acceptance: { ok: false }, humanTest: green }), false);
});

test("renderCard without a human-test row renders the three-row card, not a throw", () => {
  const rendered = renderCard({ num: 4242, ok: true, valueOk: true, valueWhy: "f", diffOk: true, diffWhy: "f", acceptance: null });
  assert.doesNotMatch(rendered, /Human test/);
  assert.match(rendered, /\| \*\*Diff\*\* \| ✅ \| f \|/);
});
