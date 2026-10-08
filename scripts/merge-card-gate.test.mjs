// Unit tests for merge-card-gate.mjs: the value-fsm verdict + wait logic (issue #655), the
// acceptance row over closing-issue bodies (issue #712), and the architecture + security pass rows
// bound to the head sha. Run: node --test scripts/merge-card-gate.test.mjs
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
  passMarkers,
  passRow,
  cardOk,
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

// ── the ARCHITECTURE and SECURITY pass rows — pure over PR comments, bound to the head sha ──────

const HEAD = "1111111111111111111111111111111111111111";
const OLD = "2222222222222222222222222222222222222222";
const MAINTAINERS = new Set(["maint", "admin-person"]);
const isMaintainer = (login) => MAINTAINERS.has(login);
const comment = (login, body, extra = {}) => ({ user: { login, type: "User" }, body, html_url: `https://example.test/c/${login}`, ...extra });
const marker = (kind, sha, verdict, extra = "") => `<!-- vexa-pass:${kind} sha=${sha} verdict=${verdict}${extra ? " " + extra : ""} -->`;
const opts = { head: HEAD, isMaintainer };

test("pass markers: parses kind, full sha, verdict and extra fields; ignores prose that names no marker", () => {
  const body = [
    "Architecture pass done.",
    marker("architecture", HEAD, "pass"),
    marker("security", HEAD, "pass", "findings=0"),
    "no marker here: vexa-pass:architecture sha=deadbeef",
  ].join("\n");
  assert.deepEqual(passMarkers(body), [
    { kind: "architecture", sha: HEAD, verdict: "pass", fields: { sha: HEAD, verdict: "pass" } },
    { kind: "security", sha: HEAD, verdict: "pass", fields: { sha: HEAD, verdict: "pass", findings: "0" } },
  ]);
  assert.deepEqual(passMarkers(""), []);
  assert.deepEqual(passMarkers(undefined), []);
});

test("RED, no marker: the row fails and names exactly what is missing", () => {
  for (const kind of ["architecture", "security"]) {
    const row = passRow(kind, [comment("maint", "LGTM")], opts);
    assert.equal(row.ok, false);
    assert.equal(row.state, "missing");
    assert.match(row.why, new RegExp(`^no ${kind} pass on head 1111111`));
    assert.match(row.why, new RegExp(`<!-- vexa-pass:${kind} sha=<full head sha> verdict=pass -->`));
    assert.match(row.why, /write or admin/);
  }
  assert.equal(passRow("architecture", [], opts).ok, false);
  assert.equal(passRow("architecture", null, opts).ok, false);
});

test("RED, stale sha: a pass for an older head does not count, and the row says re-run", () => {
  const row = passRow("architecture", [comment("maint", marker("architecture", OLD, "pass"))], opts);
  assert.equal(row.ok, false);
  assert.equal(row.state, "stale");
  assert.match(row.why, /^architecture pass on record is for 2222222, head is 1111111 — re-run/);
});

test("RED, non-maintainer: a marker from an account without write or admin does not count", () => {
  const row = passRow("security", [comment("drive-by", marker("security", HEAD, "pass"))], opts);
  assert.equal(row.ok, false);
  assert.equal(row.state, "missing");
  assert.match(row.why, /@drive-by/);
  assert.match(row.why, /does not count/);
});

test("GREEN, pass: a maintainer's marker for the current head passes the row", () => {
  const row = passRow("architecture", [comment("maint", marker("architecture", HEAD, "pass"))], opts);
  assert.equal(row.ok, true);
  assert.equal(row.state, "pass");
  assert.match(row.why, /^pass on head 1111111 by @maint/);
  // the head match is case-insensitive (a sha pasted in upper case is the same sha)
  assert.equal(passRow("architecture", [comment("maint", marker("architecture", HEAD.toUpperCase(), "pass"))], opts).ok, true);
});

test("GREEN, waived with waived-by: passes, and the row says waived and by whom", () => {
  const row = passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=@founder"))], opts);
  assert.equal(row.ok, true);
  assert.equal(row.state, "waived");
  assert.match(row.why, /^waived on head 1111111 by @founder \(recorded by @maint\)/);
  // waived-by elsewhere in the SAME comment also carries the waiver
  const elsewhere = passRow("security", [comment("maint", `${marker("security", HEAD, "waived")}\nwaived-by=@founder`)], opts);
  assert.equal(elsewhere.ok, true);
  assert.equal(elsewhere.state, "waived");
});

test("RED, waived without waived-by: the waiver does not pass the row", () => {
  for (const body of [marker("security", HEAD, "waived"), marker("security", HEAD, "waived", "waived-by=")]) {
    const row = passRow("security", [comment("maint", body)], opts);
    assert.equal(row.ok, false, body);
    assert.equal(row.state, "invalid");
    assert.match(row.why, /waived-by=/);
  }
});

test("the kinds do not substitute: a security pass never clears the architecture row", () => {
  const comments = [comment("maint", marker("security", HEAD, "pass"))];
  assert.equal(passRow("security", comments, opts).ok, true);
  assert.equal(passRow("architecture", comments, opts).ok, false);
});

test("the newest maintainer verdict on head wins: a later fail supersedes an earlier pass", () => {
  const row = passRow("security", [
    comment("maint", marker("security", HEAD, "pass")),
    comment("admin-person", marker("security", HEAD, "fail", "findings=2")),
  ], opts);
  assert.equal(row.ok, false);
  assert.equal(row.state, "invalid");
  assert.match(row.why, /verdict=fail/);
  assert.match(row.why, /2 finding/);
});

test("the merge card's own comment never counts, even when it quotes a marker for the head", () => {
  const card = comment("github-actions[bot]", `<!-- merge-card -->\n${marker("architecture", HEAD, "pass")}`, { user: { login: "maint", type: "User" } });
  assert.equal(passRow("architecture", [card], opts).ok, false);
});

test("a short sha is named as the defect, not reported as a stale pass", () => {
  const row = passRow("architecture", [comment("maint", marker("architecture", HEAD.slice(0, 7), "pass"))], opts);
  assert.equal(row.ok, false);
  assert.match(row.why, /full 40-character head sha/);
});

test("the card: both rows render, and the verdict fails without both passes", () => {
  const base = { num: 4242, valueOk: true, valueWhy: "fixture", diffOk: true, diffWhy: "fixture", acceptance: null };
  const architecture = passRow("architecture", [comment("maint", marker("architecture", HEAD, "pass"))], opts);
  const securityMissing = passRow("security", [], opts);
  const securityWaived = passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=@founder"))], opts);

  assert.equal(cardOk({ ...base, architecture, security: securityMissing }), false);
  assert.equal(cardOk({ ...base, architecture: securityMissing, security: securityWaived }), false);
  assert.equal(cardOk({ ...base, architecture, security: securityWaived }), true);
  // the passes never buy the other rows
  assert.equal(cardOk({ ...base, valueOk: false, architecture, security: securityWaived }), false);

  const red = renderCard({ ...base, architecture, security: securityMissing, ok: false });
  assert.match(red, /^<!-- merge-card -->\n/);
  assert.match(red, /\| \*\*Architecture\*\* \| ✅ \| pass on head 1111111 by @maint/);
  assert.match(red, /\| \*\*Security\*\* \| ❌ \| no security pass on head 1111111/);
  assert.match(red, /\*\*Not mergeable yet\*\*/);

  const green = renderCard({ ...base, architecture, security: securityWaived, ok: true });
  assert.match(green, /\| \*\*Security\*\* \| ✅ waived \| waived on head 1111111 by @founder/);
  assert.match(green, /\*\*Ready to merge\*\*/);
  // the rows sit after the existing ones, Architecture before Security
  const lines = green.split("\n");
  const at = (label) => lines.findIndex((l) => l.startsWith(`| **${label}**`));
  assert.ok(at("Diff") < at("Architecture") && at("Architecture") < at("Security"));
});

test("the rendered card never carries a marker that a pass row would accept", () => {
  const rendered = renderCard({
    num: 4242, ok: false, valueOk: true, valueWhy: "x", diffOk: true, diffWhy: "x", acceptance: null,
    architecture: passRow("architecture", [], opts), security: passRow("security", [], opts),
  });
  assert.deepEqual(passMarkers(rendered).filter((m) => m.sha.toLowerCase() === HEAD), []);
});
