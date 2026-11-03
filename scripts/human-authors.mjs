#!/usr/bin/env node
// human-authors: commits and pull requests here have human authors only (ruling
// authors-humans-only-1103). Fails when any commit in BASE..HEAD, or the PR title or body,
// carries a Co-Authored-By trailer naming Claude or Anthropic, an author or committer at
// anthropic.com or named Claude, or the generated-by footer.
//
//   node scripts/human-authors.mjs BASE HEAD [--event PATH]
//
// PATH is the GitHub event payload (GITHUB_EVENT_PATH); its pull_request title and body are
// checked too. Exit 0 when clean, 1 on any finding. Needs no secrets.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const TRAILER = /^\s*co-authored-by:.*\b(claude|anthropic)\b/i;
const FOOTER = /generated with \[?claude code|\u{1F916}\s*generated with/iu;
const EMAIL = /(@|\.)anthropic\.com\s*$/i;
const NAME = /^\s*claude(\s*code|\s*\[bot\])?\s*$/i;

/** [{pattern, line}] for each offending line of a message. */
export function scanText(text) {
  const out = [];
  for (const raw of String(text ?? "").split(/\r?\n/)) {
    if (TRAILER.test(raw)) out.push({ pattern: "co-authored-by-claude", line: raw.trim() });
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
  for (const field of ["title", "body"]) for (const f of scanText(pr[field])) out.push({ where: `pr-${field}`, ...f });
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
