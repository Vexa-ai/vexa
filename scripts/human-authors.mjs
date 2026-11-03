#!/usr/bin/env node
// human-authors: commits and pull requests here have human authors only (ruling
// authors-humans-only-1103). Fails when any commit in BASE..HEAD, or the PR title or body,
// carries a Co-Authored-By trailer naming Claude or Anthropic, an author or committer at
// anthropic.com or named Claude, or the generated-by footer.
//
//   node scripts/human-authors.mjs BASE HEAD [--event PATH]
//
// PATH is the GitHub event payload (GITHUB_EVENT_PATH); its pull_request title and body are
// checked too, outside closed code fences (a quoted example is not a mark). Exit 0 when clean,
// 1 on any finding. Needs no secrets.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// git reads "Co-authored-by : x" as a trailer too, so any spacing before the colon counts.
const TRAILER = /^\s*co-authored-by\s*:(.*)$/i;
// The footer starts its line; the same words inside a sentence are prose about it.
const FOOTER = /^[\s*_]*(?:\u{1F916}\s*generated with|generated with \[?claude code)/iu;
const EMAIL = /(@|\.)anthropic\.com\s*$/i;
const ANTHROPIC_ADDRESS = /[@.]anthropic\.com\b/i;
// A machine name: Claude alone, Claude Code, claude[bot], Claude plus a model ("Claude Opus 4.5",
// "Claude 3.5 Sonnet"), or Anthropic. A person named Claude Monet or Jean-Claude passes.
const NAME = new RegExp("^\\s*(?:claude(?:\\s*\\[bot\\]"
  + "|[\\s-]+(?:\\d[\\d.]*\\s+)?(?:code|opus|sonnet|haiku|fable|instant|ai|bot|agent)\\b.*"
  + "|[\\s-]+\\d[\\d.]*\\b.*)?|.*\\banthropic\\b.*)\\s*$", "i");
const FENCE = /^\s*(```|~~~)/;

/** True when a Co-authored-by value is Claude or Anthropic, not a person. */
const machine = (value) => ANTHROPIC_ADDRESS.test(value) || NAME.test(value.split("<")[0]);

/** The lines of text outside closed code fences. An unclosed fence hides nothing. */
export function unfenced(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const m = FENCE.exec(lines[i]);
    if (m) {
      const end = lines.findIndex((l, j) => j > i && l.trimStart().startsWith(m[1]));
      if (end >= 0) { i = end; continue; }
    }
    out.push(lines[i]);
  }
  return out;
}

/** [{pattern, line}] for each offending line of a message. */
export function scanText(text, { skipFences = false } = {}) {
  const out = [];
  for (const raw of skipFences ? unfenced(text) : String(text ?? "").split(/\r?\n/)) {
    const trailer = TRAILER.exec(raw);
    if (trailer && machine(trailer[1])) out.push({ pattern: "co-authored-by-claude", line: raw.trim() });
    else if (FOOTER.test(raw)) out.push({ pattern: "generated-with", line: raw.trim() });
  }
  return out;
}

/** commit: {sha, an, ae, cn, ce, body} → [{where, pattern, line}] */
export function scanCommit(c) {
  const out = [];
  for (const [role, name, email] of [["author", c.an, c.ae], ["committer", c.cn, c.ce]]) {
    if (EMAIL.test(email ?? "")) out.push({ where: c.sha, pattern: `${role}-email-anthropic`, line: `${name} <${email}>` });
    if (NAME.test(name ?? "")) out.push({ where: c.sha, pattern: `${role}-name-claude`, line: `${name} <${email}>` });
  }
  for (const f of scanText(c.body)) out.push({ where: c.sha, ...f });
  return out;
}

export function scanPr(event) {
  const pr = event?.pull_request ?? {};
  const out = [];
  for (const field of ["title", "body"]) {
    for (const f of scanText(pr[field], { skipFences: true })) out.push({ where: `pr-${field}`, ...f });
  }
  return out;
}

export function readCommits(base, head, cwd = process.cwd()) {
  const raw = execFileSync("git", ["log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e", `${base}..${head}`],
    { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return raw.split("\x1e").map((r) => r.replace(/^\n+|\n+$/g, "")).filter(Boolean).map((r) => {
    const [sha, an, ae, cn, ce, ...rest] = r.split("\x00");
    return { sha, an, ae, cn, ce, body: rest.join("\x00") };
  });
}

function main(argv) {
  const args = [...argv];
  let event = null;
  const i = args.indexOf("--event");
  if (i >= 0) { event = JSON.parse(readFileSync(args[i + 1], "utf8")); args.splice(i, 2); }
  if (args.length !== 2) { console.error("usage: human-authors.mjs BASE HEAD [--event PATH]"); return 2; }
  const [base, head] = args;
  const commits = readCommits(base, head);
  const findings = [...commits.flatMap(scanCommit), ...scanPr(event)];
  for (const f of findings) console.log(`${f.where}\t${f.pattern}\t${f.line}`);
  console.log(`human-authors: ${commits.length} commit(s) in ${base.slice(0, 12)}..${head.slice(0, 12)}, ${findings.length} finding(s)`);
  return findings.length ? 1 : 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
