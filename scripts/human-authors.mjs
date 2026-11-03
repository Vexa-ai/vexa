#!/usr/bin/env node
// human-authors: commits and pull requests here have human authors only (ruling
// authors-humans-only-1103). Fails when any commit in BASE..HEAD, or the PR title or body,
// carries a Co-Authored-By trailer naming Claude or Anthropic, an author or committer that is
// Claude or Anthropic (by name, an anthropic.com address or the claude[bot] GitHub address), or
// the generated-by footer.
//
//   node scripts/human-authors.mjs BASE HEAD [--event PATH]
//
// PATH is the GitHub event payload (GITHUB_EVENT_PATH); its pull_request title and body are
// checked too, outside closed code fences and inline code spans (a quoted example is not a mark).
// Exit 0 when clean, 1 on any finding, 2 when the range cannot be read. Needs no secrets.
//
// The Python twin (scripts/human_authors.py in the other repos) gives the same verdict on every
// input: the same line splitter, whitespace set and ASCII-only case folding on both sides.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// One whitespace set on both sides (JS \s without the line breaks), written out so the Python
// twin can use the very same class.
const WS = " \\t\\f\\v\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff";
const WS_STRIP = new RegExp(`^[${WS}\\n\\r]+|[${WS}\\n\\r]+$`, "g");
const SEPARATORS = new RegExp(`^[${WS}\\n\\r_-]+`); // skipped between Claude and the word after it
const NAME_STRIP =new RegExp(`^[${WS}\\n\\r"'\\u201c\\u201d\\u2018\\u2019]+|[${WS}\\n\\r"'\\u201c\\u201d\\u2018\\u2019]+$`, "g");
// No u flag on the keyword patterns: case folding stays ASCII-only, as Python's re.A.
// Only CR, LF and CRLF end a line.
const LINE_BREAK = /\r\n|\r|\n/;
// git reads "Co-authored-by : x" as a trailer too, so any spacing before the colon counts.
const TRAILER = new RegExp(`^[${WS}]*co-authored-by[${WS}]*:`, "i");
// The footer: the robot then "generated with" anywhere; the linked form anywhere; the plain form
// only where it starts its line (after quote, list, numbered-list, emphasis or <sub> markup). The same plain
// words inside a sentence are prose about it.
const FOOTER_ROBOT = new RegExp(`\\uD83E\\uDD16\\uFE0F?[${WS}]*generated[${WS}]+with`, "i");
const FOOTER_LINK = new RegExp(`generated[${WS}]+with[${WS}]+\\[claude[${WS}]+code\\]\\(`, "i");
const FOOTER_PLAIN = new RegExp(`^(?:[${WS}>*_+-]|[0-9]+[.)])*(?:<sub>[${WS}*_]*)?`
  + `generated[${WS}]+with[${WS}]+\\[?claude[${WS}]+code`, "i");
const ANTHROPIC_ADDRESS = /[@.]anthropic\.com\b/i;
const CLAUDE_BOT_ADDRESS = /claude\[bot\]@users\.noreply\.github\.com\b/i;
const CLAUDE = /^claude/i;
const ANTHROPIC = /anthropic/gi;
const MODEL_WORD = /^(?:code|opus|sonnet|haiku|fable|mythos|instant|ai|bot|agent|assistant|model)/i;
const NOREPLY = /^no-?reply$/i;
const LETTER = /^[\p{L}\p{M}]$/u;
const OPEN_FENCE = /^ {0,3}(`{3,}|~{3,})([\s\S]*)$/;
const CLOSE_FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;

const strip = (s) => s.replace(WS_STRIP, "");
/** A letter or a combining mark (Unicode L* or M*), as unicodedata.category in Python. */
const letter = (ch) => LETTER.test(ch);
const cpAt = (s, i) => String.fromCodePoint(s.codePointAt(i));
function cpBefore(s, i) {
  const lo = s.charCodeAt(i - 1);
  if (lo >= 0xdc00 && lo <= 0xdfff && i >= 2) {
    const hi = s.charCodeAt(i - 2);
    if (hi >= 0xd800 && hi <= 0xdbff) return s.slice(i - 2, i);
  }
  return s[i - 1];
}

/** True when pattern occurs in name with no letter right before or after it. */
function hasWord(name, pattern) {
  for (const m of name.matchAll(pattern)) {
    const start = m.index, end = m.index + m[0].length;
    const before = start ? cpBefore(name, start) : "";
    const after = end < name.length ? cpAt(name, end) : "";
    if (!(before && letter(before)) && !(after && letter(after))) return true;
  }
  return false;
}

/** An anthropic.com address (any subdomain) or the claude[bot] GitHub noreply address. */
export function machineAddress(text) {
  if (ANTHROPIC_ADDRESS.test(text ?? "")) return "anthropic";
  if (CLAUDE_BOT_ADDRESS.test(text ?? "")) return "claude-bot";
  return null;
}

/** True when a name is Claude or Anthropic: "Claude", "Claude Code", "Claude Opus 5", "Claude (AI)",
 *  "claude[bot]", "Claude-Code", "Jane (Anthropic)"; or a name starting with the word Claude at a
 *  noreply address. After Claude, spaces, hyphens and underscores are skipped; then the end, another
 *  non-letter, or a whole model word is machine. "Claude Monet", "Claude Aiken",
 *  "Claude-Michel Schönberg" and "Jean-Claude Killy" pass. */
export function machineName(name, email = "") {
  const n = String(name ?? "").replace(NAME_STRIP, "");
  if (hasWord(n, ANTHROPIC)) return true;
  if (!CLAUDE.test(n) || (n.length > 6 && letter(cpAt(n, 6)))) return false; // "Claudette", "Jean-Claude"
  const word = n.slice(6).replace(SEPARATORS, "");
  if (!word || !letter(cpAt(word, 0))) return true;   // "Claude", "Claude 3.5 Sonnet", "Claude (AI)", "Claude.ai"
  const model = MODEL_WORD.exec(word);
  if (model && (model[0].length === word.length || !letter(cpAt(word, model[0].length)))) {
    return true;                                       // "Claude Opus 5", "Claude-Code", "Claude Sonnet4", not "Claude Botha"
  }
  const local = strip(String(email ?? "")).split("@")[0];
  return NOREPLY.test(strip(local));
}

/** 'Name <email>' → [name, email]. No '<' means the whole value is the name. */
function splitIdentity(value) {
  const lt = value.indexOf("<");
  if (lt < 0) return [value, ""];
  const rest = value.slice(lt + 1);
  const gt = rest.indexOf(">");
  return [value.slice(0, lt), gt >= 0 ? rest.slice(0, gt) : rest];
}

/** True when a Co-authored-by value is Claude or Anthropic, not a person. */
const machine = (value) => Boolean(machineAddress(value) || machineName(...splitIdentity(value)));

const footer = (line) => FOOTER_ROBOT.test(line) || FOOTER_LINK.test(line) || FOOTER_PLAIN.test(line);

export const splitLines = (text) => String(text ?? "").split(LINE_BREAK);

function closes(line, fence) {
  const m = CLOSE_FENCE.exec(line);
  return Boolean(m) && m[1][0] === fence[0] && m[1].length >= fence.length;
}

/** The lines outside closed code fences (CommonMark: 3+ backticks or tildes indented 0–3 spaces,
 *  closed by the same character at least as long with nothing after it but spaces or tabs; a
 *  backtick fence's info string holds no backtick). An unclosed fence hides nothing. */
export function unfenced(lines) {
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const m = OPEN_FENCE.exec(lines[i]);
    if (m && !(m[1][0] === "`" && m[2].includes("`"))) {
      const end = lines.findIndex((l, j) => j > i && closes(l, m[1]));
      if (end >= 0) { i = end; continue; }
    }
    out.push(lines[i]);
  }
  return out;
}

/** The line without its inline code spans (a run of n backticks up to the next run of exactly
 *  n). A run with no closing run is literal text. */
export function dropCodeSpans(line) {
  let out = "", i = 0;
  const n = line.length;
  while (i < n) {
    if (line[i] !== "`") { out += line[i]; i++; continue; }
    let j = i;
    while (j < n && line[j] === "`") j++;
    let k = j, close = -1;
    while (k < n) {
      if (line[k] !== "`") { k++; continue; }
      let m = k;
      while (m < n && line[m] === "`") m++;
      if (m - k === j - i) { close = m; break; }
      k = m;
    }
    if (close < 0) { out += line.slice(i, j); i = j; } else i = close;
  }
  return out;
}

/** git folds a line that starts with a space or tab into the trailer above it. */
const continuation = (line) => (line[0] === " " || line[0] === "\t") && strip(line) !== "";

/** [{pattern, line}] for each offending line of a message. skipFences: PR text, where closed code
 *  fences and inline code spans are quoted examples, not marks. */
export function scanText(text, { skipFences = false } = {}) {
  const lines = splitLines(text);
  const pairs = skipFences ? unfenced(lines).map((l) => [l, dropCodeSpans(l)]) : lines.map((l) => [l, l]);
  const out = [];
  pairs.forEach(([shown, line], i) => {
    const trailer = TRAILER.exec(line);
    if (trailer) {
      let value = line.slice(trailer[0].length), whole = strip(shown);
      for (const [laterShown, later] of pairs.slice(i + 1)) {
        if (!continuation(later)) break;
        value += " " + strip(later);
        whole += " " + strip(laterShown);
      }
      if (machine(value)) { out.push({ pattern: "co-authored-by-claude", line: whole }); return; }
    }
    if (footer(line)) out.push({ pattern: "generated-with", line: strip(shown) });
  });
  return out;
}

/** commit: {sha, an, ae, cn, ce, body} → [{where, pattern, line}] */
export function scanCommit(c) {
  const out = [];
  for (const [role, name, email] of [["author", c.an, c.ae], ["committer", c.cn, c.ce]]) {
    const address = machineAddress(email);
    if (address) out.push({ where: c.sha, pattern: `${role}-email-${address}`, line: `${name} <${email}>` });
    if (machineName(name, email)) out.push({ where: c.sha, pattern: `${role}-name-claude`, line: `${name} <${email}>` });
  }
  for (const f of scanText(c.body)) out.push({ where: c.sha, ...f });
  return out;
}

export function scanPr(event) {
  const pr = event?.pull_request ?? {};
  const out = [];
  for (const field of ["title", "body"]) {
    for (const f of scanText(pr[field] || "", { skipFences: true })) out.push({ where: `pr-${field}`, ...f });
  }
  return out;
}

export class MalformedRecord extends Error {}

/** One `git show -s --format=%an%x00%ae%x00%cn%x00%ce%x00%B` record → commit. A commit message
 *  cannot hold a NUL, so the first four NULs are the separators. */
export function parseRecord(sha, raw) {
  const cuts = [];
  for (let p = -1; cuts.length < 4;) {
    p = raw.indexOf("\0", p + 1);
    if (p < 0) break;
    cuts.push(p);
  }
  if (cuts.length !== 4) {
    throw new MalformedRecord(`commit ${sha}: git show printed ${cuts.length} NUL separator(s), expected 4; `
      + "cannot read its author and message");
  }
  const [an, ae, cn, ce] = [0, 1, 2, 3].map((k) => raw.slice(k ? cuts[k - 1] + 1 : 0, cuts[k]));
  return { sha, an, ae, cn, ce, body: raw.slice(cuts[3] + 1) };
}

class GitFailed extends Error {}

function git(args, cwd) {
  try {
    return execFileSync("git", args,
      { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
  } catch (e) {
    throw new GitFailed(`git ${args.join(" ")} failed: ${String(e.stderr ?? e.message).trim()}`);
  }
}

export function readCommits(base, head, cwd = process.cwd()) {
  const shas = git(["rev-list", `${base}..${head}`], cwd).split(/\s+/).filter(Boolean);
  // log.showSignature would print gpg lines ahead of the record
  return shas.map((sha) => parseRecord(sha, git(["-c", "log.showSignature=false", "show", "-s",
    "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha], cwd)));
}

function main(argv) {
  const args = [...argv];
  let event = null;
  const i = args.indexOf("--event");
  if (i >= 0) { event = JSON.parse(readFileSync(args[i + 1], "utf8")); args.splice(i, 2); }
  if (args.length !== 2) { console.error("usage: human-authors.mjs BASE HEAD [--event PATH]"); return 2; }
  const [base, head] = args;
  let commits;
  try {
    commits = readCommits(base, head);
  } catch (e) {
    if (!(e instanceof MalformedRecord || e instanceof GitFailed)) throw e;
    console.error(`human-authors: ${e.message}`);
    return 2;
  }
  const findings = [...commits.flatMap(scanCommit), ...scanPr(event)];
  for (const f of findings) console.log(`${f.where}\t${f.pattern}\t${f.line}`);
  console.log(`human-authors: ${commits.length} commit(s) in ${base.slice(0, 12)}..${head.slice(0, 12)}, ${findings.length} finding(s)`);
  return findings.length ? 1 : 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
