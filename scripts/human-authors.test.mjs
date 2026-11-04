import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import * as ha from "./human-authors.mjs";
import { guardTree } from "./test-tree.mjs";

const { scanCommit, scanPr } = ha;

guardTree();

const HUMAN = {
  sha: "a".repeat(40), an: "DmitriyG228", ae: "2280905@gmail.com", cn: "DmitriyG228", ce: "2280905@gmail.com",
  body: "fix: a thing\n\nSigned-off-by: DmitriyG228 <2280905@gmail.com>\n",
};
const commit = (kw) => ({ ...HUMAN, ...kw });
const patterns = (c) => scanCommit(c).map((f) => f.pattern);

test("a clean commit passes", () => assert.deepEqual(scanCommit(HUMAN), []));
test("a human Co-authored-by trailer passes", () =>
  assert.deepEqual(scanCommit(commit({ body: HUMAN.body + "Co-authored-by: Jane Doe <jane@example.com>\n" })), []));
test("a Co-Authored-By trailer naming Claude fails", () =>
  assert.deepEqual(patterns(commit({ body: HUMAN.body + "Co-Authored-By: Claude <noreply@example.com>\n" })), ["co-authored-by-claude"]));
test("a co-authored-by trailer naming Anthropic fails, any case", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nco-authored-by: Bot <bot@ANTHROPIC.com>\n" })), ["co-authored-by-claude"]));
test("an author email at anthropic.com fails", () =>
  assert.deepEqual(patterns(commit({ ae: "noreply@anthropic.com" })), ["author-email-anthropic"]));
test("a committer email at a subdomain of anthropic.com fails", () =>
  assert.deepEqual(patterns(commit({ ce: "x@mail.Anthropic.com" })), ["committer-email-anthropic"]));
test("an author named Claude fails", () => assert.deepEqual(patterns(commit({ an: "Claude" })), ["author-name-claude"]));
test("a committer named claude fails", () => assert.deepEqual(patterns(commit({ cn: "claude" })), ["committer-name-claude"]));
test("the generated-with footer fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n" })), ["generated-with"]));
test("the robot generated-with marker fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\n\u{1F916} Generated with some tool\n" })), ["generated-with"]));
const withTrailer = (t) => commit({ body: HUMAN.body + t + "\n" });
test("a space before the colon still fails", () =>
  assert.deepEqual(patterns(withTrailer("Co-Authored-By : Claude <noreply@anthropic.com>")), ["co-authored-by-claude"]));
test("a tab and spaces before the colon still fail", () =>
  assert.deepEqual(patterns(withTrailer("co-authored-by\t  :Claude Opus 4.5 <bot@example.com>")), ["co-authored-by-claude"]));
test("a human co-author named Claude Monet passes", () =>
  assert.deepEqual(scanCommit(withTrailer("Co-authored-by: Claude Monet <monet@example.com>")), []));
test("a human co-author named Jean-Claude passes", () =>
  assert.deepEqual(scanCommit(withTrailer("Co-authored-by: Jean-Claude Killy <jck@example.com>")), []));
test("a co-author named for a Claude model fails", () =>
  assert.deepEqual(patterns(withTrailer("Co-Authored-By: Claude 3.5 Sonnet <bot@example.com>")), ["co-authored-by-claude"]));
test("a claude[bot] co-author fails", () =>
  assert.deepEqual(patterns(withTrailer("Co-authored-by: claude[bot] <1+claude[bot]@users.noreply.github.com>")), ["co-authored-by-claude"]));
test("a human name with an anthropic.com address fails", () =>
  assert.deepEqual(patterns(withTrailer("Co-authored-by: Jane Doe <jane@anthropic.com>")), ["co-authored-by-claude"]));
test("an author named Claude Monet passes", () =>
  assert.deepEqual(scanCommit(commit({ an: "Claude Monet", cn: "Jean-Claude" })), []));
test("an author named for a Claude model fails", () =>
  assert.deepEqual(patterns(commit({ an: "Claude Opus 4.5" })), ["author-name-claude"]));
test("the footer named inside a sentence passes", () =>
  assert.deepEqual(scanCommit(commit({ body: "docs: the check rejects the Generated with [Claude Code] footer\n" })), []));

test("a finding carries the sha and the line", () =>
  assert.deepEqual(scanCommit(commit({ body: "x\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n" })),
    [{ where: "a".repeat(40), pattern: "co-authored-by-claude", line: "Co-Authored-By: Claude Opus <noreply@anthropic.com>" }]));

test("a clean PR title and body pass", () => assert.deepEqual(scanPr({ pull_request: { title: "ci: add check", body: "Plain." } }), []));
test("the footer in a PR body fails", () =>
  assert.deepEqual(scanPr({ pull_request: { title: "t", body: "Text\n\nGenerated with Claude Code" } }).map((f) => f.pattern), ["generated-with"]));
test("a trailer in a PR title fails", () =>
  assert.deepEqual(scanPr({ pull_request: { title: "Co-Authored-By: Claude", body: null } }).map((f) => f.where), ["pr-title"]));
const QUOTED = "Co-Authored-By: Claude <noreply@anthropic.com>\n"
  + "\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)\n";
const body = (b) => ({ pull_request: { title: "t", body: b } });
test("marks quoted in a backtick fence in a PR body pass", () =>
  assert.deepEqual(scanPr(body("The check blocks:\n\n```\n" + QUOTED + "```\n")), []));
test("marks quoted in a tilde fence in a PR body pass", () =>
  assert.deepEqual(scanPr(body("~~~text\n" + QUOTED + "~~~\nDone.")), []));
test("an unclosed fence hides nothing", () =>
  assert.deepEqual(scanPr(body("```\n" + QUOTED)).map((f) => f.pattern), ["co-authored-by-claude", "generated-with"]));
test("a trailer after a closed fence fails", () =>
  assert.deepEqual(scanPr(body("```\nx\n```\nCo-Authored-By: Claude <noreply@anthropic.com>")).map((f) => f.pattern), ["co-authored-by-claude"]));
// v2 changed this verdict: the robot then "generated with" counts anywhere on a line.
test("prose naming the robot footer in a PR body fails", () =>
  assert.deepEqual(scanPr(body("This blocks a `Co-Authored-By: Claude` trailer and the \u{1F916} Generated with footer.")).map((f) => f.pattern),
    ["generated-with"]));

const coAuthor = (value) => patterns(commit({ body: HUMAN.body + "Co-authored-by: " + value + "\n" }));
const prPatterns = (b, title = "t") => scanPr({ pull_request: { title, body: b } }).map((f) => f.pattern);

test("a co-author at the claude[bot] noreply address fails", () =>
  assert.deepEqual(coAuthor("Dmitriy <209825114+claude[bot]@users.noreply.github.com>"), ["co-authored-by-claude"]));
test("a co-author at the claude[bot] noreply address without the id fails", () =>
  assert.deepEqual(coAuthor("Dmitriy <claude[bot]@users.noreply.github.com>"), ["co-authored-by-claude"]));
test("an author email at the claude[bot] noreply address fails", () =>
  assert.deepEqual(patterns(commit({ ae: "209825114+claude[bot]@users.noreply.github.com" })), ["author-email-claude-bot"]));
test("a committer email at the claude[bot] noreply address fails", () =>
  assert.deepEqual(patterns(commit({ ce: "claude[bot]@USERS.noreply.github.com" })), ["committer-email-claude-bot"]));
test("a human at a GitHub noreply address passes", () =>
  assert.deepEqual(coAuthor("Jane Doe <123+jane@users.noreply.github.com>"), []));

const MACHINE_CO_AUTHORS = [
  "Claude Assistant <noreply@example.com>", "Claude (AI) <x@example.com>", "Claude.ai <x@example.com>",
  "\"Claude Opus 5\" <x@example.com>", "Claude Sonnet4 <x@example.com>", "Claude Mythos <x@example.com>",
  "Claude-Code <x@example.com>", "Claude <monet@example.com>", "Claude Monet <noreply@example.com>",
  "Jane (Anthropic) <jane@example.com>", "Claude Bot <x@example.com>",
];
for (const v of MACHINE_CO_AUTHORS) {
  test(`a co-author ${v} fails`, () => assert.deepEqual(coAuthor(v), ["co-authored-by-claude"]));
}
for (const v of ["Claudette Colbert <cc@example.com>", "Camille Claudel <cc@example.com>",
  "Claude Aiken <ca@example.com>", "Claude Botha <cb@example.com>", "Claude-Michel Sch\u00f6nberg <cms@example.com>"]) {
  test(`a co-author ${v} passes`, () => assert.deepEqual(coAuthor(v), []));
}
test("a committer named Claude Haiku 4 fails", () =>
  assert.deepEqual(patterns(commit({ cn: "Claude Haiku 4" })), ["committer-name-claude"]));
test("an author Claude Monet at a no-reply address fails", () =>
  assert.deepEqual(patterns(commit({ an: "Claude Monet", ae: "no-reply@example.com" })), ["author-name-claude"]));

test("a continued trailer is judged whole", () =>
  assert.deepEqual(scanCommit(commit({ body: HUMAN.body + "Co-authored-by: Dmitriy\n <noreply@anthropic.com>\n" })),
    [{ where: "a".repeat(40), pattern: "co-authored-by-claude", line: "Co-authored-by: Dmitriy <noreply@anthropic.com>" }]));
test("a tab continuation fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nCo-authored-by:\n\tClaude <x@example.com>\n" })), ["co-authored-by-claude"]));
test("a continued human trailer passes", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nCo-authored-by: Jane Doe\n <jane@example.com>\n" })), []));
test("a blank line ends the trailer", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\nCo-authored-by: Claude\n  \n Monet <monet@example.com>\n" })), ["co-authored-by-claude"]));
test("a continued trailer in a PR body fails", () =>
  assert.deepEqual(prPatterns("Co-authored-by: Dmitriy\n <noreply@anthropic.com>"), ["co-authored-by-claude"]));

const FOOTERS = [
  "- Generated with Claude Code", "1. Generated with Claude Code", "2) Generated with Claude Code",
  "+ Generated with [Claude Code]", "> Generated with Claude Code",
  "<sub>Generated with Claude Code</sub>",
  "\u{1F916}\ufe0f Generated with [Claude Code](https://claude.com/claude-code)",
  "**\u{1F916} Generated with** Claude Code",
  "Thanks. Generated with [Claude Code](https://claude.com/claude-code)",
  "Done. \u{1F916} Generated with Claude Code",
  "> <sub>\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)</sub>",
];
for (const v of FOOTERS) {
  test(`the footer ${JSON.stringify(v)} in a commit fails`, () =>
    assert.deepEqual(patterns(commit({ body: "x\n\n" + v + "\n" })), ["generated-with"]));
  test(`the footer ${JSON.stringify(v)} in a PR body fails`, () =>
    assert.deepEqual(prPatterns("Text\n\n" + v), ["generated-with"]));
}
test("the plain footer words mid-sentence pass", () =>
  assert.deepEqual(patterns(commit({ body: "docs: this was not generated with Claude Code.\n" })), []));

const TRAILER_LINE = "Co-Authored-By: Claude <noreply@anthropic.com>";
test("a lone CR before a trailer in a PR body fails", () =>
  assert.deepEqual(prPatterns("Text\r" + TRAILER_LINE), ["co-authored-by-claude"]));
test("a lone CR before a trailer in a commit fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\r\r" + TRAILER_LINE + "\r" })), ["co-authored-by-claude"]));
test("a CRLF trailer in a commit fails", () =>
  assert.deepEqual(patterns(commit({ body: "x\r\n\r\n" + TRAILER_LINE + "\r\n" })), ["co-authored-by-claude"]));
// git and GitHub end a line only at CR or LF, so a trailer after any of these is mid-line.
for (const sep of ["\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\x0b", "\x0c"]) {
  test(`${JSON.stringify(sep)} does not end a line`, () =>
    assert.deepEqual(patterns(commit({ body: "x" + sep + TRAILER_LINE + "\n" })), []));
}

const BOTH = ["co-authored-by-claude", "generated-with"];
test("a fence indented four spaces is not a fence", () =>
  assert.deepEqual(prPatterns("Example:\n\n    ```\n    " + TRAILER_LINE + "\n    ```\n"), ["co-authored-by-claude"]));
test("a fence indented three spaces hides", () => assert.deepEqual(prPatterns("   ```\n" + QUOTED + "   ```\n"), []));
test("a tab-indented fence is not a fence", () => assert.deepEqual(prPatterns("\t```\n" + QUOTED + "\t```\n"), BOTH));
test("a longer closing fence closes", () => assert.deepEqual(prPatterns("````\n" + QUOTED + "`````\n"), []));
test("a shorter closing fence does not close", () => assert.deepEqual(prPatterns("````\n" + QUOTED + "```\n"), BOTH));
test("a closing fence with text after it does not close", () =>
  assert.deepEqual(prPatterns("```\n" + QUOTED + "``` end\n"), BOTH));
test("the other fence character does not close", () => assert.deepEqual(prPatterns("```\n" + QUOTED + "~~~\n"), BOTH));
test("a backtick in a backtick fence's info string is not a fence", () =>
  assert.deepEqual(prPatterns("```a`b\n" + QUOTED + "```\n"), BOTH));
test("a backtick in a tilde fence's info string is a fence", () =>
  assert.deepEqual(prPatterns("~~~a`b\n" + QUOTED + "~~~\n"), []));
test("an inline-code mention in a PR body passes", () =>
  assert.deepEqual(prPatterns("We block `" + TRAILER_LINE + "` and `\u{1F916} Generated with` lines."), []));
test("inline code at the start of a PR body line passes", () =>
  assert.deepEqual(prPatterns("`" + TRAILER_LINE + "`\n``\u{1F916} Generated with ` x``"), []));
test("inline code in a PR title passes", () => assert.deepEqual(prPatterns("", "`" + TRAILER_LINE + "`"), []));
test("an unmatched backtick hides nothing", () =>
  assert.deepEqual(prPatterns("`\u{1F916} Generated with Claude Code"), ["generated-with"]));
test("inline code in a commit message is not skipped", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\n`\u{1F916} Generated with Claude Code`\n" })), ["generated-with"]));
test("a fence in a commit message is not skipped", () =>
  assert.deepEqual(patterns(commit({ body: "x\n\n```\n" + QUOTED + "```\n" })), BOTH));


// Commits are read raw (git cat-file commit); anything not readable exactly is a hard error.
const RAW_HEAD = "tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
  + "author Jane <jane@example.com> 1700000000 +0000\n"
  + "committer Jane <jane@example.com> 1700000000 +0000\n";
const RAW_MSG = "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n";
const refuse = (raw, message) => assert.throws(() => ha.parseCommit("b".repeat(40), raw), message);
test("a raw commit with 0x1e keeps the whole message", () => {
  const c = ha.parseCommit("b".repeat(40), Buffer.from(RAW_HEAD + "\nfeat: x\x1e y\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"));
  assert.deepEqual([c.an, c.ce], ["Jane", "jane@example.com"]);
  assert.deepEqual(patterns(c), ["co-authored-by-claude"]);
});
test("an IBM037 encoding header is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD + "encoding IBM037\n\n" + RAW_MSG), /commit b{40}: declares encoding IBM037; only UTF-8/));
test("a UTF-16LE encoding header is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD + "encoding UTF-16LE\n\n" + RAW_MSG), /declares encoding UTF-16LE/));
test("a UTF-8 encoding header is read", () =>
  assert.deepEqual(patterns(ha.parseCommit("b".repeat(40), Buffer.from(RAW_HEAD + "encoding utf-8\n\n" + RAW_MSG))), ["co-authored-by-claude"]));
test("a NUL in the message is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD + "\nfeat: x\0\n\nCo-Authored-By: Claude\n"), /holds a NUL byte/));
test("a NUL in the author is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD.replace("author Jane", "author Claude\0x") + "\nx\n"), /holds a NUL byte/));
test("an invalid UTF-8 message is a hard error", () =>
  refuse(Buffer.concat([Buffer.from(RAW_HEAD + "\nfeat: "), Buffer.from([0xff, 0x0a])]), /the message is not valid UTF-8/));
test("an author without an address is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD.replace("Jane <jane@example.com> 1700000000", "Claude 1700000000") + "\nx\n"), /the author line has no <address>/));
test("a missing committer is a hard error", () =>
  refuse(Buffer.from(RAW_HEAD.split("committer")[0] + "\nx\n"), /1 author and 0 committer line/));
test("signature continuation lines are not headers", () => {
  const head = RAW_HEAD + "gpgsig -----BEGIN PGP SIGNATURE-----\n author Claude <noreply@anthropic.com> 1 +0000\n -----END PGP SIGNATURE-----\n";
  assert.deepEqual(scanCommit(ha.parseCommit("b".repeat(40), Buffer.from(head + "\nfeat: x\n"))), []);
});

test("a co-author at @claude's id noreply address fails", () =>
  assert.deepEqual(coAuthor("Dmitriy <81847+claude@users.noreply.github.com>"), ["co-authored-by-claude"]));
test("a co-author at @claude's noreply address fails", () =>
  assert.deepEqual(coAuthor("Dmitriy <claude@users.noreply.github.com>"), ["co-authored-by-claude"]));
test("a co-author at @claude's account id fails", () =>
  assert.deepEqual(coAuthor("Dmitriy <81847+dmitriy@users.noreply.github.com>"), ["co-authored-by-claude"]));
test("an author email at @claude's noreply address fails", () =>
  assert.deepEqual(patterns(commit({ ae: "81847+claude@users.noreply.github.com" })), ["author-email-claude-user"]));
test("a committer email at @claude's noreply address fails", () =>
  assert.deepEqual(patterns(commit({ ce: "CLAUDE@users.noreply.github.com" })), ["committer-email-claude-user"]));
test("an author email at claude[bot]'s account id fails", () =>
  assert.deepEqual(patterns(commit({ ae: "209825114+x@users.noreply.github.com" })), ["author-email-claude-bot"]));
for (const v of ["Jane <123+claudette@users.noreply.github.com>", "Jane <notclaude@users.noreply.github.com>",
  "Jane <181847+jane@users.noreply.github.com>"]) {
  test(`a co-author ${v} passes`, () => assert.deepEqual(coAuthor(v), []));
}

// Each is a Claude trailer to git (git interpret-trailers --parse), with CR as git reads it.
const GIT_CR_TRAILERS = [
  "Co-authored-by:\rClaude <noreply@anthropic.com>",
  "Co-authored-by: Dmitriy\r<noreply@anthropic.com>",
  "Co-authored-by: Dmitriy\n\r<noreply@anthropic.com>",
  "Co-authored-by: Dmitriy\r<209825114+claude[bot]@users.noreply.github.com>",
];
const gitTrailerValues = (message) => execFileSync("git", ["interpret-trailers", "--parse"], { input: message, encoding: "utf8" })
  .split("\n").filter((l) => l.toLowerCase().startsWith("co-authored-by:")).map((l) => l.slice(l.indexOf(":") + 1));
for (const v of GIT_CR_TRAILERS) {
  test(`${JSON.stringify(v)} fails as git reads it`, () => {
    const message = "feat: x\n\n" + v + "\n";
    assert.ok(gitTrailerValues(message).some((t) => ha.machine(t)), "git reads no Claude trailer");
    assert.deepEqual(patterns(commit({ body: message })), ["co-authored-by-claude"]);
  });
}

const MORE_MACHINE_CO_AUTHORS = [
  "\u0421laude <x@example.com>", "\uff23\uff4c\uff41\uff55\uff44\uff45 <x@example.com>",
  "C\u200blaude Code <x@example.com>", "Claude\u034fCode <x@example.com>", "Claude\ufe0fCode <x@example.com>",
  "(Claude) <x@example.com>", "\u00abClaude\u00bb <x@example.com>", "ClaudeCode <x@example.com>",
  "ClaudeAI <x@example.com>", "Jane (Anthr\u03bfpic) <x@example.com>", "Jane (anthropics) <x@example.com>",
  "Claude, AI <x@example.com>", "Claude (paris) <x@example.com>", "Claude (Opus) <x@example.com>",
  "Claude, Jean <noreply@example.com>",
];
for (const v of MORE_MACHINE_CO_AUTHORS) {
  test(`a co-author ${JSON.stringify(v)} fails`, () => assert.deepEqual(coAuthor(v), ["co-authored-by-claude"]));
}
for (const v of ["Claudemir Silva <cs@example.com>", "Claude, Jean <jean@example.com>", "Claude (Paris) <cp@example.com>"]) {
  test(`a co-author ${v} passes`, () => assert.deepEqual(coAuthor(v), []));
}
test("an author named Cyrillic Claude Code fails", () =>
  assert.deepEqual(patterns(commit({ an: "\u0421laude Code", ae: "x@example.com" })), ["author-name-claude"]));
test("an author named Claude, Jean passes", () =>
  assert.deepEqual(patterns(commit({ an: "Claude, Jean", ae: "jean@gmail.com" })), []));

const PR_SHAPES = [
  ["see `x\n`\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)`", "generated-with"],
  ["see `x\n`Co-authored-by: Claude <noreply@anthropic.com>`", "co-authored-by-claude"],
  ["<https://x/`>\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)<https://y/`>", "generated-with"],
  ["Text <a title=\"`\"></a>\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)<a title=\"`\"></a>", "generated-with"],
  ["<a title=\"`\"></a>\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)<a title=\"`\"></a>", "generated-with"],
  ["\\`Co-authored-by: Claude <noreply@anthropic.com>\\`", "co-authored-by-claude"],
  ["<!--\n```\n-->\n\u{1F916} Generated with [Claude Code](https://claude.com/claude-code)\n<!--\n```\n-->", "generated-with"],
  ["<div>\n```\nCo-authored-by: Claude <noreply@anthropic.com>\n```\n</div>", "co-authored-by-claude"],
  ["> Co-authored-by: Claude <noreply@anthropic.com>", "co-authored-by-claude"],
  ["- Co-authored-by: Claude <noreply@anthropic.com>", "co-authored-by-claude"],
  ["\u{1F916} **Generated** with [Claude Code](https://claude.com/claude-code)", "generated-with"],
  ["## Generated with Claude Code", "generated-with"],
  ["| Generated with Claude Code |", "generated-with"],
  ["\u{1F916}\u200d Generated with Claude Code", "generated-with"],
  ["\u{1F916} _Generated with Claude Code_", "generated-with"],
  ["Generated with [Claude&nbsp;Code](https://claude.com/claude-code)", "generated-with"],
  ["\u{1F916} Generated&#32;with [Claude Code](https://claude.com/claude-code)", "generated-with"],
  ["\u{1F916} Generated\nwith [Claude Code](https://claude.com/claude-code)", "generated-with"],
  ["Generated with Claude\u200bCode", "generated-with"],
  [":robot: Generated with Claude Code", "generated-with"],
  ["Generated with <a href=\"https://claude.com/claude-code\">Claude Code</a>", "generated-with"],
  ["Generated with **[Claude Code](https://claude.com/claude-code)**", "generated-with"],
];
for (const [b, p] of PR_SHAPES) {
  test(`the PR body ${JSON.stringify(b)} fails`, () => assert.deepEqual(prPatterns(b), [p]));
}
const PR_GUARDS = [
  "<!-- Describe the change -->\nFixes the parser.", "<div>x</div>\n\n```\n" + QUOTED + "```\n",
  "Use <kbd>Ctrl</kbd> and `\u{1F916} Generated with` text.", "See https://claude.com/claude-code for the tool.",
];
for (const b of PR_GUARDS) {
  test(`the PR body ${JSON.stringify(b)} passes`, () => assert.deepEqual(prPatterns(b), []));
}

const PR_OPENERS = [
  { login: "claude[bot]", type: "Bot", id: 209825114 }, { login: "claude", type: "User", id: 81847 },
  { login: "Claude" }, { login: "claude-code-action[bot]", type: "Bot", id: 208546643 },
  { login: "renamed", type: "User", id: 81847 },
];
const openedBy = (user) => scanPr({ pull_request: { title: "t", body: "b", user } }).map((f) => [f.where, f.pattern]);
test("the opener finding names the account", () =>
  assert.deepEqual(scanPr({ pull_request: { user: PR_OPENERS[0] } }),
    [{ where: "pr-user", pattern: "opened-by-claude", line: "claude[bot] (Bot, 209825114)" }]));
for (const u of PR_OPENERS) {
  test(`a PR opened by ${JSON.stringify(u)} fails`, () => assert.deepEqual(openedBy(u), [["pr-user", "opened-by-claude"]]));
}
for (const u of [{ login: "claudette", type: "User", id: 5 }, { login: "jane-claude", type: "User", id: 6 }]) {
  test(`a PR opened by ${JSON.stringify(u)} passes`, () => assert.deepEqual(openedBy(u), []));
}

// End to end over a real git range, in a private repository under the OS temp dir.
const SCRIPT = fileURLToPath(new URL("./human-authors.mjs", import.meta.url));
function repo() {
  const dir = mkdtempSync(join(tmpdir(), "human-authors-"));
  const git = (args, env = {}) => execFileSync("git", args, { cwd: dir, encoding: "utf8", env: { ...process.env, ...env } }).trim();
  // plumbing, so no commit hook of the host judges the fixture
  let tip = null;
  const mk = (msg, name = "Jane", email = "jane@example.com") => {
    const tree = git(["hash-object", "-t", "tree", "-w", "/dev/null"]);
    tip = git(["commit-tree", tree, ...(tip ? ["-p", tip] : []), "-m", msg],
      { GIT_AUTHOR_NAME: name, GIT_AUTHOR_EMAIL: email, GIT_COMMITTER_NAME: name, GIT_COMMITTER_EMAIL: email });
    return tip;
  };
  git(["init", "-q"]);
  const run = (base, head, event) => {
    const args = [SCRIPT, base, head];
    if (event) { const p = join(dir, "event.json"); writeFileSync(p, JSON.stringify(event)); args.push("--event", p); }
    return spawnSync(process.execPath, args, { cwd: dir, encoding: "utf8" });
  };
  // a commit written byte for byte with git hash-object, on top of the current tip
  const mkRaw = (header = "", message = "feat: x\n", literally = false) => {
    const tree = git(["hash-object", "-t", "tree", "-w", "/dev/null"]);
    const raw = `tree ${tree}\nparent ${tip}\nauthor Jane <jane@example.com> 1700000000 +0000\n`
      + `committer Jane <jane@example.com> 1700000000 +0000\n${header}\n${message}`;
    tip = execFileSync("git", ["hash-object", "-t", "commit", "-w", "--stdin", ...(literally ? ["--literally"] : [])],
      { cwd: dir, input: raw, encoding: "utf8" }).trim();
    return tip;
  };
  return { dir, git, mk, mkRaw, tip: () => tip, run, base: mk("base"), done: () => rmSync(dir, { recursive: true, force: true }) };
}

test("a clean range exits 0", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x\n\nCo-authored-by: Bob <bob@example.com>");
    const out = r.run(r.base, head, { pull_request: { title: "x", body: "y" } });
    assert.equal(out.status, 0, out.stdout + out.stderr);
  } finally { r.done(); }
});

test("an offending commit is printed with its sha and patterns", () => {
  const r = repo();
  try {
    const bad = r.mk("feat: x", "Claude", "noreply@anthropic.com");
    const head = r.mk("feat: y");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1);
    assert.match(out.stdout, new RegExp(bad));
    assert.match(out.stdout, /author-email-anthropic/);
    assert.match(out.stdout, /author-name-claude/);
  } finally { r.done(); }
});

test("a spaced trailer fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x\n\nCo-Authored-By : Claude <noreply@anthropic.com>");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1);
    assert.match(out.stdout, /co-authored-by-claude/);
  } finally { r.done(); }
});

test("a PR body alone fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x");
    const out = r.run(r.base, head, { pull_request: { title: "x", body: "\u{1F916} Generated with X" } });
    assert.equal(out.status, 1);
    assert.match(out.stdout, /pr-body/);
  } finally { r.done(); }
});

test("a 0x1e in a message then a Claude trailer fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x\x1e more\n\nCo-Authored-By: Claude <noreply@anthropic.com>");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1, out.stdout + out.stderr);
    assert.match(out.stdout, /co-authored-by-claude/);
    assert.match(out.stdout, /1 commit\(s\)/);
  } finally { r.done(); }
});

test("a 0x1e in clean messages counts each commit once", () => {
  const r = repo();
  try {
    r.mk("feat: x\x1e more");
    const head = r.mk("feat: y\x1e\x1e");
    const out = r.run(r.base, head);
    assert.equal(out.status, 0, out.stdout + out.stderr);
    assert.match(out.stdout, /2 commit\(s\)/);
  } finally { r.done(); }
});

test("a claude[bot] author fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x", "Dmitriy", "209825114+claude[bot]@users.noreply.github.com");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1);
    assert.match(out.stdout, /author-email-claude-bot/);
  } finally { r.done(); }
});

test("an unreadable range is a hard error", () => {
  const r = repo();
  try {
    const out = r.run(r.base, "no-such-ref");
    assert.equal(out.status, 2, out.stdout + out.stderr);
    assert.match(out.stderr, /human-authors: git rev-list/);
  } finally { r.done(); }
});

test("a CR-after-colon trailer fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x\n\nCo-authored-by:\rClaude <noreply@anthropic.com>");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1, out.stdout + out.stderr);
    assert.match(out.stdout, /co-authored-by-claude/);
    assert.ok(!out.stdout.includes("\r"));
  } finally { r.done(); }
});

test("a @claude author fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x", "Dmitriy", "claude@users.noreply.github.com");
    const out = r.run(r.base, head);
    assert.equal(out.status, 1);
    assert.match(out.stdout, /author-email-claude-user/);
  } finally { r.done(); }
});

test("an IBM037 commit is a hard error", () => {
  const r = repo();
  try {
    const tree = r.git(["hash-object", "-t", "tree", "-w", "/dev/null"]);
    const head = r.git(["-c", "i18n.commitEncoding=IBM037", "commit-tree", tree, "-p", r.tip(),
      "-m", "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>"],
    { GIT_AUTHOR_NAME: "Claude", GIT_AUTHOR_EMAIL: "209825114+claude[bot]@users.noreply.github.com",
      GIT_COMMITTER_NAME: "Jane", GIT_COMMITTER_EMAIL: "jane@example.com" });
    const out = r.run(r.base, head);
    assert.equal(out.status, 2, out.stdout + out.stderr);
    assert.match(out.stderr, /declares encoding IBM037/);
  } finally { r.done(); }
});

test("a UTF-16LE commit is a hard error", () => {
  const r = repo();
  try {
    const head = r.mkRaw("encoding UTF-16LE\n", "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n");
    const out = r.run(r.base, head);
    assert.equal(out.status, 2, out.stdout + out.stderr);
    assert.match(out.stderr, /declares encoding UTF-16LE/);
  } finally { r.done(); }
});

test("a NUL in a message is a hard error", () => {
  const r = repo();
  try {
    const head = r.mkRaw("", "feat: x\0\n\nCo-authored-by: Claude <noreply@anthropic.com>\n", true);
    const out = r.run(r.base, head);
    assert.equal(out.status, 2, out.stdout + out.stderr);
    assert.match(out.stderr, /holds a NUL byte/);
  } finally { r.done(); }
});

test("a PR opened by claude[bot] fails the range", () => {
  const r = repo();
  try {
    const head = r.mk("feat: x");
    const out = r.run(r.base, head, { pull_request: { title: "x", body: "y", user: { login: "claude[bot]", type: "Bot" } } });
    assert.equal(out.status, 1);
    assert.match(out.stdout, /pr-user\topened-by-claude/);
  } finally { r.done(); }
});
