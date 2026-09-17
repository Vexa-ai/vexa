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
  prNumbersFromEventPayload,
  prNumbersFromEnv,
} from "./merge-card-gate.mjs";

const run = (o) => ({ name: "value-fsm", started_at: "2026-07-16T20:07:14Z", ...o });

const commentEventFixture = JSON.parse(readFileSync(new URL("./fixtures/merge-card-issue-comment-event.json", import.meta.url), "utf8"));
const commentWorkflow = readFileSync(new URL("../.github/workflows/merge-card-comment.yml", import.meta.url), "utf8");
const gateWorkflow = readFileSync(new URL("../.github/workflows/merge-card.yml", import.meta.url), "utf8");

test("fixture-pr-number: PR comments resolve and plain issue comments do not", () => {
  for (const key of ["onPullRequest", "onIssue"]) assert.ok(Object.hasOwn(commentEventFixture, key));
  assert.equal(commentEventFixture.onIssue.issue.number, 4243);
  assert.equal(commentEventFixture.onIssue.issue.pull_request, undefined);
  assert.deepEqual(prNumbersFromEventPayload("issue_comment", commentEventFixture.onPullRequest), [4242]);
  assert.deepEqual(prNumbersFromEventPayload("issue_comment", commentEventFixture.onIssue), []);
});

test("helper-total: missing and malformed payloads never throw", () => {
  const events = [
    undefined, null, {}, { issue: {} }, { issue: { pull_request: {} } },
    { issue: { number: 0, pull_request: {} } },
    { issue: { number: 7, pull_request: null } },
    "garbage", 7, [],
    { get issue() { throw new Error("malformed accessor"); } },
  ];
  // Asserted in two steps on purpose: "it threw" and "it returned the wrong thing" are different
  // failures, and this is the one test whose whole subject is that it never throws.
  for (const [i, event] of events.entries()) {
    let got;
    assert.doesNotThrow(() => { got = prNumbersFromEventPayload("issue_comment", event); }, `threw on events[${i}]`);
    assert.deepEqual(got, [], `wrong result for events[${i}]`);
  }
  for (const eventName of ["push", undefined, null]) {
    assert.deepEqual(prNumbersFromEventPayload(eventName, commentEventFixture.onPullRequest), []);
  }
  for (const number of [undefined, null, 0, -1, NaN, Infinity, "nope", Symbol("number"), {}]) {
    assert.deepEqual(prNumbersFromEventPayload("issue_comment", { issue: { number, pull_request: {} } }), []);
    assert.deepEqual(prNumbersFromEventPayload("pull_request", { pull_request: { number } }), []);
  }
});

test("helper-total: PR events and merge groups resolve deduped numbers in order", () => {
  for (const eventName of ["pull_request", "pull_request_target", "pull_request_review"]) {
    assert.deepEqual(prNumbersFromEventPayload(eventName, { pull_request: { number: 7 } }), [7]);
    assert.deepEqual(prNumbersFromEventPayload(eventName, { pull_request: { number: "8" } }), [8]);
    for (const event of [undefined, null, {}, { pull_request: {} }])
      assert.deepEqual(prNumbersFromEventPayload(eventName, event), []);
  }
  assert.deepEqual(prNumbersFromEventPayload("issue_comment", { issue: { number: "7", pull_request: {} } }), [7]);
  for (const [head_ref, expected] of [
    ["gh-readonly-queue/main/pr-620-abc", [620]],
    ["gh-readonly-queue/main/pr-620-abc/pr-621-def/pr-620-ghi", [620, 621]],
    [undefined, []], ["unmatched", []], [42, []],
  ]) assert.deepEqual(prNumbersFromEventPayload("merge_group", { merge_group: { head_ref } }), expected);
  assert.deepEqual(prNumbersFromEventPayload("merge_group"), []);
});

test("env-precedence: explicit numbers and parsed merge refs bypass the event reader", () => {
  let calls = 0;
  const readEvent = () => { calls++; return commentEventFixture.onPullRequest; };
  const env = { GITHUB_EVENT_NAME: "issue_comment", GITHUB_EVENT_PATH: "/does/not/exist" };
  assert.deepEqual(prNumbersFromEnv({ ...env, PR_NUMBERS: "7" }, { readEvent }), [7]);
  assert.equal(calls, 0);
  const MERGE_GROUP_REF = "gh-readonly-queue/main/pr-620-abc/pr-621-def/pr-620-ghi";
  assert.deepEqual(prNumbersFromEnv({ ...env, PR_NUMBERS: " 7 7 8 nope 0 ", MERGE_GROUP_REF }, { readEvent }), [7, 8]);
  assert.equal(calls, 0);
  assert.deepEqual(prNumbersFromEnv({ ...env, MERGE_GROUP_REF }, { readEvent }), [620, 621]);
  assert.equal(calls, 0);
  assert.deepEqual(prNumbersFromEnv({ ...env, PR_NUMBERS: " ", MERGE_GROUP_REF: "unparsable" }, { readEvent }), [4242]);
  assert.equal(calls, 1);
  assert.deepEqual(prNumbersFromEnv({}, { readEvent }), []);
  assert.equal(calls, 1);
});

test("failsoft-reader: unreadable or malformed events yield empty resolution", () => {
  const env = { GITHUB_EVENT_NAME: "issue_comment", GITHUB_EVENT_PATH: "/nope.json" };
  for (const readEvent of [
    () => { throw new Error("EACCES"); },
    () => undefined,
    () => "unparsable garbage",
  ]) assert.doesNotThrow(() => assert.deepEqual(prNumbersFromEnv(env, { readEvent }), []));
  assert.doesNotThrow(() => assert.deepEqual(prNumbersFromEnv(env), []));
  for (const env of [undefined, null, {}, { PR_NUMBERS: 7 }])
    assert.doesNotThrow(() => assert.deepEqual(prNumbersFromEnv(env), []));
});

test("env-precedence: the event reader receives the configured path", () => {
  let pathRead;
  assert.deepEqual(prNumbersFromEnv({
    GITHUB_EVENT_NAME: "issue_comment", GITHUB_EVENT_PATH: "event.json",
  }, { readEvent: (path) => { pathRead = path; return commentEventFixture.onPullRequest; } }), [4242]);
  assert.equal(pathRead, "event.json");
});

test("draft-preserved: a skip card is filtered before either write, so no comment is created OR overwritten", () => {
  // The workflow greps this exact shape out of the rendered card; pinning it here is what stops
  // the renderer drifting away from the guard silently.
  assert.match(renderCard({ num: 7, skip: "draft" }), /^_Skipped \(/m);
  // The guard returns — it is not an `else` arm — so neither write is reachable for a skip card.
  const guard = commentWorkflow.match(/^\s*if \(\/\^_Skipped \\\(\/m\.test\(body\)\).*$/m);
  assert.ok(guard, "the skip-card guard is missing from the upsert step");
  assert.match(guard[0], /\breturn;/);
  const guardAt = commentWorkflow.indexOf(guard[0]);
  for (const write of ["createComment(", "updateComment("])
    assert.ok(commentWorkflow.indexOf(write) > guardAt, `${write} must sit after the skip-card guard`);
  assert.equal([...commentWorkflow.matchAll(/createComment\(/g)].length, 1);
});

test("draft-preserved: a comment event reaches only OPEN pull requests, never a merged or closed one", () => {
  // Without the state test, a comment on any of the repo's historical PRs would post a card on it.
  assert.match(commentWorkflow, /github\.event\.issue\.state == 'open'/);
});

test("workflow-text: comment events refresh the sticky card with event-payload resolution", () => {
  assert.match(commentWorkflow, /^  issue_comment:\n    types: \[created, edited, deleted\]$/m);
  assert.match(commentWorkflow, /group: merge-card-comment-\$\{\{ github\.event\.pull_request\.number \|\| github\.event\.issue\.number \}\}/);
  assert.match(commentWorkflow, /github\.event_name == 'issue_comment' && github\.event\.issue\.pull_request != null/);
  assert.match(commentWorkflow, /github\.event_name != 'issue_comment' && !github\.event\.pull_request\.draft/);
  assert.match(commentWorkflow, /^\s+PR_NUMBERS: \$\{\{ github\.event\.pull_request\.number \}\}$/m);
  assert.match(commentWorkflow, /const issue_number = context\.payload\.pull_request\?\.number \?\? context\.payload\.issue\.number;/);
  assert.doesNotMatch(gateWorkflow, /issue_comment/);
  for (const workflow of [commentWorkflow, gateWorkflow])
    assert.match(workflow, /MERGE_CARD_REQUIRE_HUMAN_TEST: "false"/);
});

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
