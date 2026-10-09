// Unit tests for merge-card-gate.mjs: the value-fsm verdict + wait logic (issue #655), the
// acceptance row over closing-issue bodies (issue #712), the architecture + security pass rows
// bound to the head sha, and merge-card-pass.yml's choice of which run to re-run.
// Run: node --test scripts/merge-card-gate.test.mjs
//
// #655 regression: a non-terminal value-fsm run (queued|in_progress, conclusion === null) on the
// head sha was collapsed to "failure", red-carding a PR whose value-fsm was on its way to green.
// The fix makes the verdict four-state and WAITS for a terminal read before red-carding.
//
// #712 regression: PR #623's `Closes #622` auto-closed #622 with its live acceptance leg (a plain
// bullet, no ✅) undelivered — the acceptance row must catch both the checkbox and bullet shapes.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import * as gate from "./merge-card-gate.mjs";
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

test("GREEN, waived with waived-by: passes, and the row names who recorded it and whom it names", () => {
  const row = passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=@admin-person"))], opts);
  assert.equal(row.ok, true);
  assert.equal(row.state, "waived");
  assert.match(row.why, /^waived on head 1111111: waiver recorded by @maint, names @admin-person/);
  // the leading @ is optional
  assert.equal(passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=admin-person"))], opts).ok, true);
});

test("S5: waived-by counts only inside its own marker — not elsewhere in the comment, not from a sibling marker", () => {
  const elsewhere = passRow("security", [comment("maint", `${marker("security", HEAD, "waived")}\nwaived-by=@admin-person`)], opts);
  assert.equal(elsewhere.ok, false);
  assert.equal(elsewhere.state, "invalid");
  const both = [marker("architecture", HEAD, "waived", "waived-by=@admin-person"), marker("security", HEAD, "waived")].join("\n");
  assert.equal(passRow("architecture", [comment("maint", both)], opts).ok, true);
  assert.equal(passRow("security", [comment("maint", both)], opts).ok, false);
});

test("S5: waived-by must name an account with write or admin; a value that is not a login is never looked up", () => {
  const asked = [];
  const spy = (login) => { asked.push(login); return MAINTAINERS.has(login); };
  const named = passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=@founder"))], { head: HEAD, isMaintainer: spy });
  assert.equal(named.ok, false);
  assert.equal(named.state, "invalid");
  assert.match(named.why, /@founder/);
  assert.match(named.why, /write or admin/);
  for (const who of ["$(id)", "a;b", "-lead", "trail-", "x".repeat(40), "@"]) {
    const row = passRow("security", [comment("maint", marker("security", HEAD, "waived", `waived-by=${who}`))], { head: HEAD, isMaintainer: spy });
    assert.equal(row.ok, false, who);
    assert.equal(row.state, "invalid", who);
  }
  assert.deepEqual([...new Set(asked)].sort(), ["founder", "maint"], "only the commenter and a well-formed waived-by login reach the permission check");
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

test("the merge card's own comment never counts: it is skipped by its bot author, even when it quotes a marker for the head", () => {
  const everyone = () => true; // even if the bot's login passed the permission check
  const card = comment("github-actions[bot]", `<!-- merge-card -->\n${marker("architecture", HEAD, "pass")}`, { user: { login: "github-actions[bot]", type: "Bot" } });
  assert.equal(passRow("architecture", [card], { head: HEAD, isMaintainer: everyone }).ok, false);
});

test("R2-S2: a maintainer's revocation that quotes the card marker is not ignored", () => {
  const thread = [
    comment("maint", marker("security", HEAD, "pass")),
    comment("maint", `Revoking: the \`${gate.CARD_MARKER}\` comment read a stale run.\n${marker("security", HEAD, "fail")}`),
  ];
  const row = passRow("security", thread, opts);
  assert.equal(row.ok, false);
  assert.match(row.why, /verdict=fail/);
  // nor is a maintainer's pass lost for mentioning the card marker
  assert.equal(passRow("architecture", [comment("maint", `${gate.CARD_MARKER}\n${marker("architecture", HEAD, "pass")}`)], opts).ok, true);
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
  const securityWaived = passRow("security", [comment("maint", marker("security", HEAD, "waived", "waived-by=@admin-person"))], opts);

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
  assert.match(green, /\| \*\*Security\*\* \| ✅ waived \| waived on head 1111111: waiver recorded by @maint, names @admin-person/);
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

// ── S1: only a marker in the comment's normal text counts ───────────────────────────────────────

test("S1: a marker in inline code, a fenced or indented block, a quote or mid-sentence does not count", () => {
  const m = marker("architecture", HEAD, "pass");
  const quoted = [
    "Post this when done: `" + m + "`",
    "``" + m + "``",
    "```\n" + m + "\n```",
    "~~~md\n" + m + "\n~~~",
    "````\n```\n" + m + "\n```\n````", // a longer fence holds a shorter one
    "```\n" + m, //                       an unclosed fence runs to the end
    "1. ```\n   " + m + "\n   ```", //    a fence opened on a list item
    "Example:\n\n    " + m, //            indented code
    "> " + m, //                          someone else's quoted words
    "LGTM " + m, //                       mid-sentence
    // R2-S1: shapes GitHub renders as code that a narrower fence rule would miss
    "- item\n\n    ```\n  " + m + "\n    ```", // a fence opened four spaces in, on a list item's continuation
    "- item\n\n\t```\n  " + m + "\n\t```", //      the same, indented with a tab
    "  ```\n" + m + "\n  ```", //                   a fence opened two spaces in: its content may start at the margin
    "- ```\n  x\n```\n" + m + "\n```", //         the list item ends, and a new fence opens at the margin
    "```\n    ```\n" + m + "\n```", //              a closing fence indented four spaces is content, not a close
  ];
  for (const body of quoted) {
    assert.deepEqual(passMarkers(body), [], body);
    assert.equal(passRow("architecture", [comment("maint", body)], opts).ok, false, body);
  }
});

test("S1: a marker at the start of its own line counts, beside code that quotes another one", () => {
  const body = [
    "Architecture pass. The marker format is `" + marker("architecture", OLD, "pass") + "`.",
    "",
    "```",
    marker("architecture", HEAD, "fail"),
    "```",
    marker("architecture", HEAD, "pass"),
    "<!-- vexa-agent -->",
  ].join("\n");
  assert.deepEqual(passMarkers(body).map((x) => [x.sha, x.verdict]), [[HEAD, "pass"]]);
  assert.equal(passRow("architecture", [comment("maint", body)], opts).ok, true);
  // CRLF line endings, up to three spaces of indentation, and a list item's continuation line
  assert.equal(passMarkers(`intro\r\n   ${marker("security", HEAD, "pass")}\r\n`).length, 1);
  assert.equal(passMarkers(`- item\n  ${marker("security", HEAD, "pass")}`).length, 1);
});

test("R2-S1: a code block inside a quote, or a closed fence in a list, does not drop the markers after it", () => {
  const m = marker("architecture", HEAD, "pass");
  const old = marker("architecture", OLD, "fail");
  const shapes = [
    "> ```\n> " + old + "\n> ```\n" + m, //      a fence opened and closed inside a quote
    "> ```\n> " + old + "\n" + m, //                a quoted fence ends where the quote ends
    "> ```\n> code\n\n" + m, //                     a blank line ends the quote, and the fence with it
    "1. Do this:\n   ```\n   " + old + "\n   ```\n" + m, // a fence inside a list item, closed at its own indent
    "```\n" + old + "\n  ```\n" + m, //            a closing fence may be indented up to three spaces
  ];
  for (const body of shapes) {
    assert.deepEqual(passMarkers(body).map((x) => [x.sha, x.verdict]), [[HEAD, "pass"]], body);
    assert.equal(passRow("architecture", [comment("maint", body)], opts).ok, true, body);
  }
});

test("S1: a maintainer's marker for the head that does not count says where it has to stand", () => {
  const row = passRow("security", [comment("maint", "LGTM `" + marker("security", HEAD, "pass") + "`")], opts);
  assert.equal(row.ok, false);
  assert.equal(row.state, "invalid");
  assert.match(row.why, /start of its own line/);
});

// ── S2: a long thread is read newest first, and a cut history fails closed ──────────────────────

const filler = (n, at = {}) => Array.from({ length: n }, (_, i) => at[i] || comment("drive-by", `comment ${i}`));
const pager = (all) => {
  const pages = [];
  return { pages, fetchPage: (p) => { pages.push(p); return all.slice((p - 1) * 100, p * 100); } };
};

test("S2: every page of a thread under the cap is read, oldest first", () => {
  const all = filler(250);
  const { fetchPage, pages } = pager(all);
  const r = gate.readComments(1, all.length, { fetchPage, maxPages: 10 });
  assert.equal(r.truncated, false);
  assert.deepEqual(r.comments, all);
  assert.deepEqual([...pages].sort(), [1, 2, 3]);
});

test("S2: past the cap the NEWEST pages are kept, and the result says the history was cut", () => {
  const all = filler(550);
  const r = gate.readComments(1, all.length, { fetchPage: pager(all).fetchPage, maxPages: 3 });
  assert.equal(r.truncated, true);
  assert.deepEqual(r.comments, all.slice(300));
});

test("S2: comments posted after the PR's comment count was read are still read", () => {
  const all = filler(230);
  const r = gate.readComments(1, 180, { fetchPage: pager(all).fetchPage, maxPages: 10 });
  assert.equal(r.truncated, false);
  assert.deepEqual(r.comments, all);
});

test("S2: a revocation past the first 1,000 comments is read, and a pass cut off by the cap does not count", () => {
  const revoked = filler(1100, {
    0: comment("maint", marker("security", HEAD, "pass")),
    1050: comment("maint", marker("security", HEAD, "fail")),
  });
  const r = gate.readComments(1, revoked.length, { fetchPage: pager(revoked).fetchPage });
  assert.equal(passRow("security", r.comments, { ...opts, truncated: r.truncated }).ok, false);

  const cut = filler(1100, { 0: comment("maint", marker("architecture", HEAD, "pass")) });
  const r2 = gate.readComments(1, cut.length, { fetchPage: pager(cut).fetchPage });
  assert.equal(r2.truncated, true);
  const row = passRow("architecture", r2.comments, { ...opts, truncated: r2.truncated });
  assert.equal(row.ok, false);
  assert.match(row.why, /comment history too long to verify — re-post the pass/);
  // a pass inside the window still counts on a long thread
  const recent = filler(1100, { 1099: comment("maint", marker("architecture", HEAD, "pass")) });
  const r3 = gate.readComments(1, recent.length, { fetchPage: pager(recent).fetchPage });
  assert.equal(passRow("architecture", r3.comments, { ...opts, truncated: r3.truncated }).ok, true);
});

// ── S6: the verdict is bound to the head its check is posted for ────────────────────────────────

// A stand-in for the GitHub API: one PR, its comments, reviews and the maintainer set.
function fakeApi({ head, author = "maint", comments = [], reviews = [], labels = ["state: value-signed"] }) {
  const api = (path) => {
    api.calls.push(path);
    let m;
    if (/\/pulls\/\d+$/.test(path))
      return { number: 4242, draft: false, user: { login: author }, head: { sha: head }, labels: labels.map((name) => ({ name })), comments: comments.length };
    if ((m = path.match(/\/pulls\/\d+\/files\?per_page=100&page=(\d+)$/))) return m[1] === "1" ? [{ filename: "docs/x.md" }] : [];
    if ((m = path.match(/\/collaborators\/([^/]+)\/permission$/))) return { permission: MAINTAINERS.has(m[1]) ? "write" : "read" };
    if (/\/pulls\/\d+\/reviews/.test(path)) return reviews;
    if ((m = path.match(/\/issues\/\d+\/comments\?per_page=100&page=(\d+)$/))) return comments.slice((m[1] - 1) * 100, m[1] * 100);
    throw new Error(`unexpected API path ${path}`);
  };
  api.calls = [];
  return api;
}
const passes = (sha) => comment("maint", [marker("architecture", sha, "pass"), marker("security", sha, "pass")].join("\n"));

test("S6: a run judges the head its check is posted for; if the PR moved on, it fails and leaves the sticky card alone", async () => {
  const same = await gate.card(4242, { api: fakeApi({ head: HEAD, comments: [passes(HEAD)] }), expectedHead: HEAD, readClosing: () => [] });
  assert.equal(same.ok, true);

  // the run was triggered for HEAD; by the time it reads the PR, the head is OLD (with passes of its own)
  const api = fakeApi({ head: OLD, comments: [passes(OLD)] });
  const moved = await gate.card(4242, { api, expectedHead: HEAD, readClosing: () => [] });
  assert.equal(moved.ok, false);
  assert.deepEqual(moved.moved, { from: HEAD, to: OLD });
  assert.equal(api.calls.some((p) => /comments|reviews/.test(p)), false, "nothing is judged against the live head");
  const text = renderCard(moved);
  assert.ok(!text.includes(gate.CARD_MARKER), "a moved run never overwrites the sticky card");
  assert.match(text, /1111111/);
  assert.match(text, /2222222/);
});

test("S6: the Diff row reads approvals against the bound head; a merge_group run binds to the PR's head as queued", async () => {
  const approvedOn = (sha) => [{ state: "APPROVED", user: { login: "maint" }, commit_id: sha }];
  const ok = await gate.card(4242, { api: fakeApi({ head: HEAD, author: "drive-by", reviews: approvedOn(HEAD), comments: [passes(HEAD)] }), expectedHead: HEAD, readClosing: () => [] });
  assert.equal(ok.diffOk, true);
  const stale = await gate.card(4242, { api: fakeApi({ head: HEAD, author: "drive-by", reviews: approvedOn(OLD), comments: [passes(HEAD)] }), expectedHead: HEAD, readClosing: () => [] });
  assert.equal(stale.diffOk, false);
  // merge_group: no event head (the group commit is not a PR head); a push dequeues the PR
  const queued = await gate.card(4242, { api: fakeApi({ head: HEAD, comments: [passes(HEAD)] }), readClosing: () => [] });
  assert.equal(queued.ok, true);
});

test("S6: both card workflows hand the script the head their check is posted for", () => {
  for (const f of ["merge-card.yml", "merge-card-comment.yml"]) {
    const wf = readFileSync(new URL(`../.github/workflows/${f}`, import.meta.url), "utf8");
    assert.match(wf, /HEAD_SHA: \$\{\{ github\.event\.pull_request\.head\.sha \}\}/, f);
  }
  assert.match(readFileSync(new URL("./merge-card-gate.mjs", import.meta.url), "utf8"), /process\.env\.HEAD_SHA/);
});

// ── A5 and defence in depth ─────────────────────────────────────────────────────────────────────

test("A5: the card marker is one constant, and the comment workflow looks for exactly it", () => {
  assert.equal(gate.CARD_MARKER, "<!-- merge-card -->");
  const wf = readFileSync(new URL("../.github/workflows/merge-card-comment.yml", import.meta.url), "utf8");
  const literals = [...wf.matchAll(/'(<!--[^']*-->)'/g)].map((x) => x[1]);
  assert.ok(literals.length >= 2);
  assert.deepEqual([...new Set(literals)], [gate.CARD_MARKER]);
});

test("no API call goes through a shell: logins from comments never reach a command string", () => {
  const src = readFileSync(new URL("./merge-card-gate.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(src, /\bexecSync\b/);
});

// ── S3 + S4: merge-card-pass re-runs the right run, and never one awaiting approval ─────────────

const PASS_WF = readFileSync(new URL("../.github/workflows/merge-card-pass.yml", import.meta.url), "utf8");
const HAVE_JQ = (() => { try { execFileSync("jq", ["--version"], { stdio: "ignore" }); return true; } catch { return false; } })();
const NO_JQ = !HAVE_JQ && "jq is not installed";
const wfRun = (o) => ({
  id: 1, event: "pull_request", status: "completed", conclusion: "failure", display_title: "a PR title",
  pull_requests: [], head_repository: { full_name: "Vexa-ai/vexa" }, head_branch: "feature", ...o,
});

// The jq filter the workflow uses to pick a run, run against fixture run lists (API order: newest first).
function pickRun(runs, { pr = 7, prefix = "merge-card", repo = "Vexa-ai/vexa", ref = "feature", event = "" } = {}) {
  const prog = PASS_WF.match(/pick='([^']+)'/)?.[1];
  assert.ok(prog, "merge-card-pass.yml defines its run filter as pick='…'");
  return execFileSync("jq", ["-r", "--arg", "title", `${prefix}: PR #${pr}`, "--argjson", "pr", String(pr), "--arg", "repo", repo, "--arg", "ref", ref, "--arg", "event", event, prog], {
    input: JSON.stringify({ workflow_runs: runs }), encoding: "utf8",
  }).trim();
}

test("S3: the run picked is the one that judged THIS PR, not another PR sharing the head", { skip: NO_JQ }, () => {
  // both runs list both PRs: GitHub's pull_requests names every open PR matching the head, not the trigger
  const both = [{ number: 7 }, { number: 8 }];
  assert.equal(pickRun([wfRun({ id: 30, display_title: "merge-card: PR #8", pull_requests: both }), wfRun({ id: 20, display_title: "merge-card: PR #7", pull_requests: both })]), "20 rerun");
  // a run from before run-name existed: matched by its pull_requests list
  assert.equal(pickRun([wfRun({ id: 40, pull_requests: [{ number: 8 }] }), wfRun({ id: 41, pull_requests: [{ number: 7 }] })]), "41 rerun");
  // a fork run carries no pull_requests: matched by head repository and branch
  assert.equal(pickRun([wfRun({ id: 50, head_repository: { full_name: "someone/vexa" } })], { repo: "someone/vexa" }), "50 rerun");
  assert.equal(pickRun([wfRun({ id: 51, head_repository: { full_name: "other/vexa" } })], { repo: "someone/vexa" }), "");
  assert.equal(pickRun([wfRun({ id: 52, head_branch: "other" })]), "");
  // a merge-queue run is never re-run from here, and nothing to pick picks nothing
  assert.equal(pickRun([wfRun({ id: 60, event: "merge_group", display_title: "merge-card", pull_requests: [{ number: 7 }] })]), "");
  assert.equal(pickRun([]), "");
  // the comment workflow's runs carry their own name
  const sticky = { prefix: "merge-card-comment", event: "pull_request_target" };
  assert.equal(pickRun([wfRun({ id: 70, event: "pull_request_target", display_title: "merge-card-comment: PR #7" })], sticky), "70 rerun");
});

test("R2-A1: for the sticky card the pick takes the newest pull_request_target run, never a review run", { skip: NO_JQ }, () => {
  // a fork PR's review-triggered run holds a read-only token and cannot update the card
  const sticky = { prefix: "merge-card-comment", event: "pull_request_target" };
  const runs = [
    wfRun({ id: 91, event: "pull_request_review", display_title: "merge-card-comment: PR #7" }),
    wfRun({ id: 90, event: "pull_request_target", display_title: "merge-card-comment: PR #7" }),
  ];
  assert.equal(pickRun(runs, sticky), "90 rerun");
  assert.equal(pickRun([runs[0]], sticky), "");
  // the check's own workflow takes either event
  assert.equal(pickRun([wfRun({ id: 92, event: "pull_request_review", display_title: "merge-card: PR #7" })]), "92 rerun");
});

test("S4: a run awaiting approval is reported, never re-run; a run in flight is waited for", { skip: NO_JQ }, () => {
  assert.equal(pickRun([wfRun({ id: 80, display_title: "merge-card: PR #7", conclusion: "action_required" })]), "80 approval");
  assert.equal(pickRun([wfRun({ id: 81, display_title: "merge-card: PR #7", status: "in_progress", conclusion: null })]), "81 wait");
});

test("S3: the card workflows name each run after its PR, which is what the pick matches", () => {
  const card = readFileSync(new URL("../.github/workflows/merge-card.yml", import.meta.url), "utf8");
  const sticky = readFileSync(new URL("../.github/workflows/merge-card-comment.yml", import.meta.url), "utf8");
  assert.match(card, /^run-name: .*format\('merge-card: PR #\{0\}', github\.event\.pull_request\.number\)/m);
  assert.match(sticky, /^run-name: "merge-card-comment: PR #\$\{\{ github\.event\.pull_request\.number \}\}"$/m);
  assert.match(PASS_WF, /^\s+rerun merge-card\.yml merge-card ""\s/m);
  assert.match(PASS_WF, /^\s+rerun merge-card-comment\.yml merge-card-comment pull_request_target\s/m);
});

// The workflow's own shell, run with a stand-in `gh` (and `sleep`) on PATH.
function runPassJob({ pr, runs = {} }) {
  const lines = PASS_WF.split("\n");
  const at = lines.findIndex((l) => /^\s+run: \|\s*$/.test(l));
  const indent = lines[at + 1].match(/^ */)[0].length;
  const body = [];
  for (const l of lines.slice(at + 1)) { if (l.trim() && l.match(/^ */)[0].length < indent) break; body.push(l.slice(indent)); }
  const dir = mkdtempSync(join(tmpdir(), "merge-card-pass-"));
  try {
    writeFileSync(join(dir, "job.sh"), body.join("\n"));
    writeFileSync(join(dir, "fixtures.json"), JSON.stringify({ pr, runs }));
    writeFileSync(join(dir, "gh"), [
      "#!/usr/bin/env node",
      'const fs = require("fs"), path = require("path"), a = process.argv.slice(2);',
      'fs.appendFileSync(path.join(__dirname, "calls.log"), a.join(" ") + "\\n");',
      'const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures.json"), "utf8"));',
      'if (a.includes("-X")) process.exit(0);',
      "const url = a[1]; let m;",
      // an array of PRs answers successive reads in turn: the head can move while the job waits
      'if (/\\/pulls\\/\\d+$/.test(url)) {',
      '  let pr = fx.pr;',
      '  if (Array.isArray(pr)) { const f = path.join(__dirname, "pr.n"); const n = fs.existsSync(f) ? +fs.readFileSync(f, "utf8") : 0; fs.writeFileSync(f, String(n + 1)); pr = pr[Math.min(n, pr.length - 1)]; }',
      '  if (!pr) process.exit(1); process.stdout.write(JSON.stringify(pr));',
      '}',
      'else if ((m = url.match(/workflows\\/([^/]+)\\/runs/))) process.stdout.write(JSON.stringify({ workflow_runs: fx.runs[m[1]] || [] }));',
      "else process.exit(1);",
    ].join("\n"));
    writeFileSync(join(dir, "sleep"), "#!/bin/sh\nexit 0\n");
    chmodSync(join(dir, "gh"), 0o755);
    chmodSync(join(dir, "sleep"), 0o755);
    const out = execFileSync("bash", ["-e", join(dir, "job.sh")], {
      env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, REPO: "Vexa-ai/vexa", PR: "7", GH_TOKEN: "x" }, encoding: "utf8",
    });
    let calls = [];
    try { calls = readFileSync(join(dir, "calls.log"), "utf8").trim().split("\n"); } catch {}
    return { out, calls, reruns: calls.filter((c) => c.includes("-X POST")).map((c) => c.match(/runs\/(\d+)\/rerun/)[1]) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
const prJson = (sha) => ({ number: 7, head: { sha, ref: "feature", repo: { full_name: "Vexa-ai/vexa" } } });

test("S3: an empty or unreadable head stops the job before it lists any run", { skip: NO_JQ }, () => {
  for (const pr of [prJson(""), prJson(null), prJson("not-a-sha"), null]) {
    const r = runPassJob({ pr, runs: { "merge-card.yml": [wfRun({ id: 9, display_title: "merge-card: PR #7" })] } });
    assert.deepEqual(r.reruns, [], JSON.stringify(pr));
    assert.equal(r.calls.some((c) => c.includes("/runs")), false, JSON.stringify(pr));
    assert.match(r.out, /::warning ::merge-card-pass/);
  }
});

test("S3 + S4: the job re-runs this PR's runs on the head, and skips one awaiting approval", { skip: NO_JQ }, () => {
  const both = [{ number: 7 }, { number: 8 }];
  const shared = runPassJob({
    pr: prJson(HEAD),
    runs: {
      "merge-card.yml": [wfRun({ id: 31, display_title: "merge-card: PR #8", pull_requests: both }), wfRun({ id: 21, display_title: "merge-card: PR #7", pull_requests: both })],
      "merge-card-comment.yml": [wfRun({ id: 32, event: "pull_request_target", display_title: "merge-card-comment: PR #8", pull_requests: both }), wfRun({ id: 22, event: "pull_request_target", display_title: "merge-card-comment: PR #7", pull_requests: both })],
    },
  });
  assert.deepEqual(shared.reruns, ["21", "22"]);
  assert.ok(shared.calls.some((c) => c.includes(`head_sha=${HEAD}`)));

  const fork = runPassJob({
    pr: prJson(HEAD),
    runs: { "merge-card.yml": [wfRun({ id: 41, display_title: "merge-card: PR #7", conclusion: "action_required" })] },
  });
  assert.deepEqual(fork.reruns, []);
  assert.match(fork.out, /awaits approval/);
});

test("R2-S3: the head is re-read just before each re-run, and a stale head is neither re-run nor allowed to cancel the new one", { skip: NO_JQ }, () => {
  const runs = {
    "merge-card.yml": [wfRun({ id: 21, display_title: "merge-card: PR #7" })],
    "merge-card-comment.yml": [wfRun({ id: 22, event: "pull_request_target", display_title: "merge-card-comment: PR #7" })],
  };
  // the PR moves from HEAD to OLD after the job's first read
  const moved = runPassJob({ pr: [prJson(HEAD), prJson(OLD)], runs });
  assert.deepEqual(moved.reruns, []);
  assert.match(moved.out, /moved/);
  // unmoved: one PR read up front, then one before each re-run, each just before its POST
  const steady = runPassJob({ pr: prJson(HEAD), runs });
  assert.deepEqual(steady.reruns, ["21", "22"]);
  const reads = steady.calls.map((c, i) => (/\/pulls\/7$/.test(c) ? i : -1)).filter((i) => i >= 0);
  const posts = steady.calls.map((c, i) => (c.includes("-X POST") ? i : -1)).filter((i) => i >= 0);
  assert.equal(reads.length, 3);
  for (const p of posts) assert.ok(reads.includes(p - 1), "the head is read immediately before every re-run");
});

test("R2-A1, R2-A2: the comment workflow and the enforcement map say what GitHub and the card do", () => {
  const sticky = readFileSync(new URL("../.github/workflows/merge-card-comment.yml", import.meta.url), "utf8");
  const header = sticky.split("\nname:")[0];
  assert.doesNotMatch(header, /no untrusted code runs/);
  assert.match(header, /pull_request_review[\s\S]*merge commit/);
  const map = readFileSync(new URL("../docs/docs/governance/delivery.mdx", import.meta.url), "utf8");
  const row = map.split("\n").find((l) => l.startsWith("| Merge bar — value + diff accepted"));
  assert.match(row, /commit bit/);
});
