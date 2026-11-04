#!/usr/bin/env node
// human-authors: commits and pull requests here have human authors only (ruling
// authors-humans-only-1103). Fails when any commit in BASE..HEAD, or the PR title, body or opener,
// carries a Co-Authored-By trailer naming Claude or Anthropic, an author or committer that is
// Claude or Anthropic (by name, an anthropic.com address or a GitHub address of @claude or
// claude[bot]), the generated-by footer, or a PR opened by @claude or claude[bot].
//
//   node scripts/human-authors.mjs BASE HEAD [--event PATH]
//   node scripts/human-authors.mjs --event PATH        (PATH holds a merge_group event)
//
// PATH is the GitHub event payload (GITHUB_EVENT_PATH); its pull_request title, body and user are
// checked too. On a merge_group event the range is the commits the queued pull request adds, read
// as dco-range and dco-no-override read them: the queue commit is a squash GitHub writes, so the
// pull request's own commits are judged instead, from the queue base to refs/pull/<number>/head. Text inside closed code fences and inline code spans is a quoted example, not a
// mark. Commits are read raw (git cat-file); a commit that cannot be read exactly (a NUL, an
// encoding other than UTF-8, invalid UTF-8, a missing author or committer) is a hard error.
// Exit 0 when clean, 1 on any finding, 2 when the range cannot be read. Needs no secrets.
//
// The Python twin (scripts/human_authors.py in the other repos) gives the same verdict on every
// input: the same line splitters, whitespace set, ASCII-only case folding, normalisation and
// confusables table on both sides.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// One whitespace set on both sides (JS \s without the line breaks), written out so the Python
// twin can use the very same class.
const WS = " \\t\\f\\v\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff";
const WS_STRIP = new RegExp(`^[${WS}\\n\\r]+|[${WS}\\n\\r]+$`, "g");
const SEPARATORS = new RegExp(`^[${WS}\\n\\r_-]+`); // skipped between Claude and the word after it
const WS_LEAD = new RegExp(`^[${WS}\\n\\r]+`);
const EDGE = new RegExp(`^[${WS}\\n\\r\\p{P}\\p{S}]+|[${WS}\\n\\r\\p{P}\\p{S}]+$`, "gu");
// No u flag on the keyword patterns: case folding stays ASCII-only, as Python's re.A.
// PR text: only CR, LF and CRLF end a line.
const LINE_BREAK = /\r\n|\r|\n/;
// git reads "Co-authored-by : x" as a trailer too, so any spacing before the colon counts.
const TRAILER = new RegExp(`^[${WS}]*co-authored-by[${WS}]*:`, "i");
// In PR text the trailer counts behind the markup the footer may sit behind too.
const PR_TRAILER = new RegExp(`^(?:[${WS}>*_+\`#|-]|[0-9]+[.)])*co-authored-by[${WS}]*:`, "i");
// The footer: the robot (or :robot:) then "generated with" anywhere; the linked form and
// "generated with" plus the Claude Code URL anywhere; the plain form only where it starts its line,
// after quote, list, heading, table, emphasis or <sub> markup. The same plain words inside a
// sentence are prose about it.
const FOOTER_ROBOT = new RegExp(`(?:\\uD83E\\uDD16|:robot:)\\uFE0F?[${WS}]*generated[${WS}]+with`, "i");
const FOOTER_LINK = new RegExp(`generated[${WS}]+with[${WS}]+\\[claude[${WS}]*code\\]\\(`, "i");
const FOOTER_URL = new RegExp(`generated[${WS}]+with\\b[^\\n]*(?:claude\\.com/claude-code|claude\\.ai/code)`, "i");
const FOOTER_PLAIN = new RegExp(`^(?:[${WS}>*_+#|-]|[0-9]+[.)])*(?:<sub>[${WS}*_]*)?`
  + `generated[${WS}]+with[${WS}]+\\[?claude[${WS}]*code`, "i");
const ANTHROPIC_ADDRESS = /[@.]anthropic\.com\b/i;
// GitHub credits these to claude[bot] (id 209825114) and @claude (id 81847, owned by anthropics).
const CLAUDE_BOT_ADDRESS = /claude\[bot\]@users\.noreply\.github\.com\b/i;
const CLAUDE_USER_ADDRESS = /(?<![A-Za-z0-9._%+-])(?:[0-9]+\+)?claude@users\.noreply\.github\.com\b/i;
const CLAUDE_ID_ADDRESS = /(?<![0-9])(81847|209825114)\+[A-Za-z0-9._%+[\]-]*@users\.noreply\.github\.com\b/i;
const CLAUDE_IDS = [81847, 209825114];
const CLAUDE = /^claude/i;
const ANTHROPIC = /anthropics?/gi;
const MODEL_WORD = /^(?:code|opus|sonnet|haiku|fable|mythos|instant|ai|bot|agent|assistant|model)/i;
const NOREPLY = /^no-?reply$/i;
const LETTER = /^[\p{L}\p{M}]$/u;
const UPPER = /^[\p{Lu}\p{Lt}]$/u;
const IGNORABLE = /[\p{Cf}\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u3164\ufe00-\ufe0f\uffa0\u{E0000}-\u{E0FFF}]/gu;
const OPEN_FENCE = /^ {0,3}(`{3,}|~{3,})([\s\S]*)$/;
const CLOSE_FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
// An HTML block (CommonMark 4.6, read generously: any line opening with a tag-like "<"). Its lines
// are raw HTML, so a fence or code span inside one hides nothing.
const HTML_START = /^ {0,3}<(?:(script|pre|style|textarea)(?![A-Za-z0-9-])|(!--)|(\?)|(!\[CDATA\[)|(![A-Za-z])|\/?[A-Za-z])/i;
const HTML_ENDS = { 2: "-->", 3: "?>", 4: "]]>", 5: ">" };
// Inline raw HTML and autolinks (CommonMark 6.5, 6.6): backticks inside them are not code.
const INLINE_HTML = new RegExp(
  "<[A-Za-z][A-Za-z0-9.+-]{1,31}:[^<>\\x00-\\x20]*>"
  + "|<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
  + "(?:\\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*>"
  + "|<[A-Za-z][A-Za-z0-9-]*(?:[ \\t\\n]+[A-Za-z_:][A-Za-z0-9_.:-]*"
  + "(?:[ \\t\\n]*=[ \\t\\n]*(?:[^ \\t\\n\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*[ \\t\\n]*/?>"
  + "|</[A-Za-z][A-Za-z0-9-]*[ \\t\\n]*>"
  + "|<!--(?:-?>|[\\s\\S]*?-->)|<\\?[\\s\\S]*?\\?>|<!\\[CDATA\\[[\\s\\S]*?\\]\\]>|<![A-Za-z][^>]*>", "iy");
const BACKSLASH_ESCAPE = /\\([!-/:-@[-`{-~])/g;
const ENTITY = /&(?:#([0-9]{1,7})|#[xX]([0-9A-Fa-f]{1,6})|([A-Za-z][A-Za-z0-9]{1,31}));/g;
const NAMED_ENTITIES = {
  nbsp: "\u00a0", ensp: "\u2002", emsp: "\u2003", emsp13: "\u2004", emsp14: "\u2005",
  numsp: "\u2007", puncsp: "\u2008", thinsp: "\u2009", ThinSpace: "\u2009",
  hairsp: "\u200a", VeryThinSpace: "\u200a", MediumSpace: "\u205f", NewLine: "\n",
  Tab: "\t", ZeroWidthSpace: "\u200b", NegativeVeryThinSpace: "\u200b",
  NegativeThinSpace: "\u200b", NegativeMediumSpace: "\u200b", NegativeThickSpace: "\u200b",
  zwj: "\u200d", zwnj: "\u200c", NoBreak: "\u2060", shy: "\u00ad", amp: "&",
  lt: "<", gt: ">", quot: "\"", apos: "'", colon: ":", lowbar: "_",
  UnderBar: "_", ast: "*", midast: "*", grave: "`", DiacriticalGrave: "`",
  lbrack: "[", lsqb: "[", rbrack: "]", rsqb: "]", lpar: "(", rpar: ")", sol: "/",
  period: ".", excl: "!", num: "#", verbar: "|", vert: "|", VerticalLine: "|",
  commat: "@", plus: "+", hyphen: "\u2010", dash: "\u2010", equals: "=",
  quest: "?", comma: ",", semi: ";", bsol: "\\", Hat: "^", lcub: "{",
  lbrace: "{", rcub: "}", rbrace: "}", percnt: "%", dollar: "$",
};
// Cyrillic, Greek and other letters that look like the Latin ones (a small UTS #39 subset).
const CONFUSABLES = new Map(Object.entries({
  "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0443": "y",
  "\u0445": "x", "\u0455": "s", "\u0456": "i", "\u0458": "j", "\u04bb": "h", "\u04cf": "l",
  "\u0501": "d", "\u051b": "q", "\u051d": "w", "\u0410": "A", "\u0412": "B", "\u0415": "E",
  "\u041a": "K", "\u041c": "M", "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C",
  "\u0422": "T", "\u0425": "X", "\u0405": "S", "\u0406": "I", "\u0408": "J", "\u04c0": "l",
  "\u0500": "D", "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03b9": "i", "\u03bd": "v",
  "\u03c5": "u", "\u03ba": "k", "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z",
  "\u0397": "H", "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
  "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X", "\u0131": "i", "\u0251": "a",
  "\u0269": "i", "\u0585": "o", "\u057d": "u",
}));

const strip = (s) => s.replace(WS_STRIP, "");
/** A letter or a combining mark (Unicode L* or M*), as unicodedata.category in Python. */
const letter = (ch) => LETTER.test(ch);
const upper = (ch) => UPPER.test(ch);
const cpAt = (s, i) => String.fromCodePoint(s.codePointAt(i));
const dropIgnorable = (s) => s.replace(IGNORABLE, "");
const blank = (line) => strip(line) === "";
function cpBefore(s, i) {
  const lo = s.charCodeAt(i - 1);
  if (lo >= 0xdc00 && lo <= 0xdfff && i >= 2) {
    const hi = s.charCodeAt(i - 2);
    if (hi >= 0xd800 && hi <= 0xdbff) return s.slice(i - 2, i);
  }
  return s[i - 1];
}

/** NFKC, no invisible characters, look-alikes folded to Latin, no edge punctuation. */
function cleanName(name) {
  const n = dropIgnorable(String(name ?? "").normalize("NFKC"));
  return [...n].map((c) => CONFUSABLES.get(c) ?? c).join("").replace(EDGE, "");
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

/** True when s starts with a model word followed by the end or a non-letter. */
function modelAt(s) {
  const m = MODEL_WORD.exec(s);
  return Boolean(m) && (m[0].length === s.length || !letter(cpAt(s, m[0].length)));
}

/** An anthropic.com address (any subdomain), or a GitHub noreply address of claude[bot] or
 *  @claude (by name or by account id). */
export function machineAddress(text) {
  const t = text ?? "";
  if (ANTHROPIC_ADDRESS.test(t)) return "anthropic";
  if (CLAUDE_BOT_ADDRESS.test(t)) return "claude-bot";
  if (CLAUDE_USER_ADDRESS.test(t)) return "claude-user";
  const m = CLAUDE_ID_ADDRESS.exec(t);
  if (m) return m[1] === "209825114" ? "claude-bot" : "claude-user";
  return null;
}

/** True when a name is Claude or Anthropic: "Claude", "Claude Code", "Claude Opus 5", "Claude (AI)",
 *  "claude[bot]", "Claude-Code", "ClaudeCode", "Jane (Anthropic)"; or a name starting with the word
 *  Claude at a noreply address. Look-alike letters and invisible characters are folded first. After
 *  Claude, spaces, hyphens and underscores are skipped; then the end, a non-letter, or a whole model
 *  word is machine, except that a comma or a parenthesis before a capitalised name that is not a
 *  model word is a person ("Claude, Jean", "Claude (Paris)"). "Claude Monet", "Claude Aiken" and
 *  "Jean-Claude Killy" pass. */
export function machineName(name, email = "") {
  const n = cleanName(name);
  if (hasWord(n, ANTHROPIC)) return true;
  if (!CLAUDE.test(n)) return false;
  const after = n.slice(6);
  if (after && letter(cpAt(after, 0))) return modelAt(after); // "ClaudeCode", "ClaudeAI"; not "Claudette"
  const word = after.replace(SEPARATORS, "");
  if (!word) return true;                                   // "Claude", "Claude-"
  if (word[0] === "," || word[0] === "(") {
    const rest = word.slice(1).replace(WS_LEAD, "");
    if (!(rest && upper(cpAt(rest, 0)) && !modelAt(rest))) return true; // "Claude (AI)", "Claude, bot"
  } else if (!letter(cpAt(word, 0)) || modelAt(word)) {
    return true;                                            // "Claude 3.5 Sonnet", "Claude.ai", "Claude Sonnet4"
  }
  const local = strip(String(email ?? "")).split("@")[0];
  return NOREPLY.test(strip(local));
}

/** 'Name <email>' \u2192 [name, email]. No '<' means the whole value is the name. */
function splitIdentity(value) {
  const lt = value.indexOf("<");
  if (lt < 0) return [value, ""];
  const rest = value.slice(lt + 1);
  const gt = rest.indexOf(">");
  return [value.slice(0, lt), gt >= 0 ? rest.slice(0, gt) : rest];
}

/** True when a Co-authored-by value is Claude or Anthropic, not a person. */
export const machine = (value) => Boolean(machineAddress(value) || machineName(...splitIdentity(value)));

function footer(line) {
  const t = dropIgnorable(line.normalize("NFKC")).replace(/[*_]/g, "");
  return FOOTER_ROBOT.test(t) || FOOTER_LINK.test(t) || FOOTER_URL.test(t) || FOOTER_PLAIN.test(t);
}

export const splitLines = (text) => String(text ?? "").split(LINE_BREAK);

function closes(line, fence) {
  const m = CLOSE_FENCE.exec(line);
  return Boolean(m) && m[1][0] === fence[0] && m[1].length >= fence.length;
}

/** One kind per line of PR text: "hidden" (a closed code fence, CommonMark: 3+ backticks or tildes
 *  indented 0\u20133 spaces, closed by the same character at least as long with nothing after it but
 *  spaces or tabs; a backtick fence's info string holds no backtick), "html" (an HTML block),
 *  "blank" or "text". An unclosed fence hides nothing. */
export function classify(lines) {
  const kinds = [];
  let end = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (end !== null) {                                     // inside an HTML block
      if (end === "" && blank(line)) { kinds.push("blank"); end = null; } else {
        kinds.push("html");
        if (end && line.toLowerCase().includes(end)) end = null;
      }
      continue;
    }
    if (blank(line)) { kinds.push("blank"); continue; }
    const h = HTML_START.exec(line);
    if (h) {
      kinds.push("html");
      end = h[1] ? `</${h[1].toLowerCase()}>` : ([2, 3, 4, 5].map((k) => (h[k] ? HTML_ENDS[k] : null)).find(Boolean) ?? "");
      if (end && line.slice(h[0].length).toLowerCase().includes(end)) end = null;
      continue;
    }
    const m = OPEN_FENCE.exec(line);
    if (m && !(m[1][0] === "`" && m[2].includes("`"))) {
      const close = lines.findIndex((l, j) => j > i && closes(l, m[1]));
      if (close >= 0) { for (let j = i; j <= close; j++) kinds.push("hidden"); i = close; continue; }
    }
    kinds.push("text");
  }
  return kinds;
}

/** The text without its inline code spans (a run of n backticks up to the next run of exactly n,
 *  across line breaks too; each span leaves only its line breaks behind). A run with no closing
 *  run, a backslash-escaped backtick and a backtick inside an autolink or inline HTML are literal
 *  text. */
export function dropCodeSpans(text) {
  let out = "", i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (ch === "\\" && i + 1 < n) { out += text.slice(i, i + 2); i += 2; continue; }
    if (ch === "<") {
      INLINE_HTML.lastIndex = i;
      const m = INLINE_HTML.exec(text);
      if (m) { out += m[0]; i += m[0].length; continue; }
    }
    if (ch !== "`") { out += ch; i++; continue; }
    let j = i;
    while (j < n && text[j] === "`") j++;
    let k = j, close = -1;
    while (k < n) {
      if (text[k] !== "`") { k++; continue; }
      let m2 = k;
      while (m2 < n && text[m2] === "`") m2++;
      if (m2 - k === j - i) { close = m2; break; }
      k = m2;
    }
    if (close < 0) { out += text.slice(i, j); i = j; } else {
      out += "\n".repeat(text.slice(i, close).split("\n").length - 1);
      i = close;
    }
  }
  return out;
}

function entity(m, dec, hex, name) {
  if (name !== undefined) return Object.hasOwn(NAMED_ENTITIES, name) ? NAMED_ENTITIES[name] : m;
  const code = dec !== undefined ? parseInt(dec, 10) : parseInt(hex, 16);
  return code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff) ? "\ufffd" : String.fromCodePoint(code);
}

/** PR text as it renders: backslash escapes and entities resolved, invisible characters gone. */
const cleanText = (line) => dropIgnorable(line.replace(BACKSLASH_ESCAPE, "$1").replace(ENTITY, entity));

/** git folds a line that starts with a space, tab or CR into the trailer above it. */
const continuation = (line) => (line[0] === " " || line[0] === "\t" || line[0] === "\r") && strip(line) !== "";

/** [{pattern, line}] for each offending line. */
function scanLines(lines, trailer = TRAILER) {
  const out = [];
  lines.forEach((line, i) => {
    const m = trailer.exec(line);
    if (m) {
      let value = line.slice(m[0].length), whole = strip(line);
      for (const later of lines.slice(i + 1)) {
        if (!continuation(later)) break;
        value += " " + strip(later);
        whole += " " + strip(later);
      }
      if (machine(value)) { out.push({ pattern: "co-authored-by-claude", line: whole }); return; }
    }
    if (footer(line)) out.push({ pattern: "generated-with", line: strip(line) });
  });
  return out;
}

/** Every finding of first, then each finding of second that first does not already hold. */
function union(first, second) {
  const out = [...first], left = [...first];
  const same = (a, b) => a.pattern === b.pattern && a.line === b.line;
  for (const f of second) {
    const k = left.findIndex((g) => same(f, g));
    if (k >= 0) left.splice(k, 1); else out.push(f);
  }
  return out;
}

/** A commit message, read both as git reads it (lines end at LF only, a line starting with CR
 *  continues a trailer) and with CR as a line break too. */
export const scanMessage = (text) => union(scanLines(String(text ?? "").split("\n")), scanLines(splitLines(text)));

/** PR title or body, read line by line and paragraph by paragraph (a code span can cross a line
 *  break, and a soft line break renders as a space). */
export function scanPrText(text) {
  const lines = splitLines(text);
  const kinds = classify(lines);
  const byLine = [];
  lines.forEach((l, i) => {
    const k = kinds[i];
    if (k !== "hidden") byLine.push(k === "blank" ? "" : cleanText(k === "text" ? dropCodeSpans(l) : l));
  });
  const found = scanLines(byLine, PR_TRAILER);
  const byBlock = [];
  for (let i = 0; i < lines.length;) {
    const k = kinds[i];
    if (k !== "text" && k !== "html") { i++; continue; }
    let j = i;
    while (j < lines.length && kinds[j] === k) j++;
    const block = lines.slice(i, j).join("\n");
    const blockLines = (k === "text" ? dropCodeSpans(block) : block).split("\n").map(cleanText);
    const hits = scanLines(blockLines, PR_TRAILER);
    const joined = strip(blockLines.map(strip).join(" "));
    if (!hits.some((h) => h.pattern === "generated-with") && footer(joined)) hits.push({ pattern: "generated-with", line: joined });
    byBlock.push(...hits);
    i = j;
  }
  return union(found, byBlock);
}

/** commit: {sha, an, ae, cn, ce, body} \u2192 [{where, pattern, line}] */
export function scanCommit(c) {
  const out = [];
  for (const [role, name, email] of [["author", c.an, c.ae], ["committer", c.cn, c.ce]]) {
    const address = machineAddress(email);
    if (address) out.push({ where: c.sha, pattern: `${role}-email-${address}`, line: `${name} <${email}>` });
    if (machineName(name, email)) out.push({ where: c.sha, pattern: `${role}-name-claude`, line: `${name} <${email}>` });
  }
  for (const f of scanMessage(c.body)) out.push({ where: c.sha, ...f });
  return out;
}

export function scanPr(event) {
  const pr = event?.pull_request ?? {};
  const out = [];
  for (const field of ["title", "body"]) {
    for (const f of scanPrText(pr[field] || "")) out.push({ where: `pr-${field}`, ...f });
  }
  const user = pr.user || {};
  const login = String(user.login || ""), kind = user.type, uid = user.id;
  const lo = /^[\x00-\x7f]*$/.test(login) ? login.toLowerCase() : login;
  if (lo === "claude" || lo === "claude[bot]" || CLAUDE_IDS.includes(uid) || (kind === "Bot" && lo.includes("claude"))) {
    out.push({ where: "pr-user", pattern: "opened-by-claude", line: `${login} (${kind ?? "-"}, ${uid ?? "-"})` });
  }
  return out;
}

export class UnreadableCommit extends Error {}

const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
function utf8(sha, part, raw) {
  try { return UTF8.decode(raw); } catch { throw new UnreadableCommit(`commit ${sha}: the ${part} is not valid UTF-8`); }
}

function identity(sha, role, text) {
  const lt = text.indexOf("<");
  const gt = lt >= 0 ? text.indexOf(">", lt + 1) : -1;
  if (gt < 0) throw new UnreadableCommit(`commit ${sha}: the ${role} line has no <address>`);
  return [strip(text.slice(0, lt)), text.slice(lt + 1, gt)];
}

const startsWith = (buf, prefix) => buf.subarray(0, prefix.length).equals(Buffer.from(prefix));

/** A raw commit object (git cat-file commit) \u2192 commit. Anything the scanner cannot read exactly as
 *  written throws UnreadableCommit. */
export function parseCommit(sha, raw) {
  const buf = Buffer.from(raw);
  if (buf.includes(0)) throw new UnreadableCommit(`commit ${sha}: the object holds a NUL byte`);
  const cut = buf.indexOf("\n\n");
  const head = cut >= 0 ? buf.subarray(0, cut) : buf;
  const body = cut >= 0 ? buf.subarray(cut + 2) : Buffer.alloc(0);
  const headers = [];
  for (let s = 0; ;) {
    const e = head.indexOf(0x0a, s);
    if (e < 0) { headers.push(head.subarray(s)); break; }
    headers.push(head.subarray(s, e));
    s = e + 1;
  }
  const authors = headers.filter((h) => startsWith(h, "author ")).map((h) => h.subarray(7));
  const committers = headers.filter((h) => startsWith(h, "committer ")).map((h) => h.subarray(10));
  if (authors.length !== 1 || committers.length !== 1) {
    throw new UnreadableCommit(`commit ${sha}: ${authors.length} author and ${committers.length} `
      + "committer line(s), expected one of each");
  }
  for (const h of headers) {
    if (!startsWith(h, "encoding ")) continue;
    const name = strip(h.subarray(9).toString("utf8"));
    if (!["utf-8", "utf8"].includes(name.toLowerCase())) {
      throw new UnreadableCommit(`commit ${sha}: declares encoding ${name}; only UTF-8 commits can be read`);
    }
  }
  const [an, ae] = identity(sha, "author", utf8(sha, "author line", authors[0]));
  const [cn, ce] = identity(sha, "committer", utf8(sha, "committer line", committers[0]));
  return { sha, an, ae, cn, ce, body: utf8(sha, "message", body) };
}

class GitFailed extends Error {}

function git(args, cwd) {
  try {
    return execFileSync("git", args, { cwd, maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
  } catch (e) {
    throw new GitFailed(`git ${args.join(" ")} failed: ${String(e.stderr ?? e.message).trim()}`);
  }
}

export function readCommits(base, head, cwd = process.cwd()) {
  const shas = git(["rev-list", `${base}..${head}`], cwd).toString("utf8").split(/\s+/).filter(Boolean);
  return shas.map((sha) => parseCommit(sha, git(["cat-file", "commit", sha], cwd)));
}

export class UnreadableQueue extends Error {}

// GitHub names the queue branch gh-readonly-queue/<base branch>/pr-<number>-<base sha>.
const QUEUE_REF = /^(?:refs\/heads\/)?gh-readonly-queue\/.+\/pr-(\d+)-([0-9a-f]{40})$/;
const SHA = /^[0-9a-f]{40}$/;

/** merge_group event \u2192 {number, base, queue}: the queued pull request, the queue base (the queue
 *  commit's first parent) and the queue commit. */
export function queuedPullRequest(event) {
  const group = event?.merge_group ?? {};
  const m = QUEUE_REF.exec(group.head_ref ?? "");
  if (!m) throw new UnreadableQueue(`cannot read a pull request from the merge-queue ref ${JSON.stringify(group.head_ref ?? null)}`);
  if (group.base_sha !== m[2]) throw new UnreadableQueue(`merge_group.base_sha ${group.base_sha} is not the queue ref's base ${m[2]}`);
  if (!SHA.test(group.head_sha ?? "")) throw new UnreadableQueue(`merge_group.head_sha ${group.head_sha} is not a commit sha`);
  return { number: Number(m[1]), base: m[2], queue: group.head_sha };
}

const hasCommit = (rev, cwd) => {
  try { git(["cat-file", "-e", `${rev}^{commit}`], cwd); return true; } catch { return false; }
};

/** The range a merge_group run judges: the queue base to the queued pull request's head, fetched
 *  from refs/pull/<number>/head. The queue commit must sit on the queue base. */
export function mergeGroupRange(event, cwd = process.cwd()) {
  const { number, base, queue } = queuedPullRequest(event);
  for (const sha of [base, queue]) if (!hasCommit(sha, cwd)) git(["fetch", "--no-tags", "--quiet", "origin", sha], cwd);
  const parent = git(["rev-parse", `${queue}^1`], cwd).toString("utf8").trim();
  if (parent !== base) {
    throw new UnreadableQueue(`queue commit ${queue.slice(0, 12)} sits on ${parent.slice(0, 12)}, not on the queue base ${base.slice(0, 12)}`);
  }
  const ref = `refs/human-authors/pr-${number}`;
  git(["fetch", "--no-tags", "--quiet", "origin", `+refs/pull/${number}/head:${ref}`], cwd);
  const head = git(["rev-parse", `${ref}^{commit}`], cwd).toString("utf8").trim();
  return { number, base, head, queue };
}

/** A finding line with its control characters escaped, so a CR cannot hide it in a log. */
const printable = (s) => s.replace(/[\x00-\x08\x0a-\x1f\x7f]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);

function main(argv) {
  const args = [...argv];
  let event = null;
  const i = args.indexOf("--event");
  if (i >= 0) { event = JSON.parse(readFileSync(args[i + 1], "utf8")); args.splice(i, 2); }
  const queued = args.length === 0 && event?.merge_group;
  if (args.length !== 2 && !queued) {
    console.error("usage: human-authors.mjs BASE HEAD [--event PATH] | --event MERGE_GROUP_EVENT_PATH");
    return 2;
  }
  let [base, head] = args;
  let commits;
  try {
    if (queued) {
      const q = mergeGroupRange(event);
      console.log(`human-authors: merge-queue commit ${q.queue.slice(0, 12)} for #${q.number}; judging the commits the pull request adds`);
      ({ base, head } = q);
    }
    commits = readCommits(base, head);
  } catch (e) {
    if (!(e instanceof UnreadableCommit || e instanceof GitFailed || e instanceof UnreadableQueue)) throw e;
    console.error(`human-authors: ${e.message}`);
    return 2;
  }
  const findings = [...commits.flatMap(scanCommit), ...scanPr(event)];
  for (const f of findings) console.log(`${f.where}\t${f.pattern}\t${printable(f.line)}`);
  console.log(`human-authors: ${commits.length} commit(s) in ${base.slice(0, 12)}..${head.slice(0, 12)}, ${findings.length} finding(s)`);
  return findings.length ? 1 : 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
