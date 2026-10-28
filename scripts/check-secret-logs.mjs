// check-secret-logs — no log line carries any part of a credential (#1868).
//
// A key, bearer, session or signing secret may be logged by its PRESENCE or LENGTH, never by its
// value or a slice of it: `vxa_tx_` is seven characters, so `api_key[:10]` printed three secret
// ones (transcription-gateway, 2026-10-23).
//
// A log call (`logger.` `log.` `logging.` `console.` `_log.` `LOG.` + a level, `print(`,
// `log_event(`) is parsed to its closing parenthesis, following the arguments over up to
// MAX_CONTINUATION further lines. It is flagged when a CREDENTIAL REFERENCE reaches the output as
//   - an f-string / template-literal interpolation: `f"{api_key}"`, `${req.apiKey}`;
//   - a positional or keyword argument: `logger.info("key %s", api_key)`, `console.log("t", token)`,
//     `log_event("x", token=token)`;
//   - an operand of `+`, `%`, `||`, `??` or `or`: `"Bearer " + token`, `"%s" % secret`;
//   - the argument of `str()`, `repr()`, `String()` or `"...".format()`.
// A credential reference is a name or attribute whose last segment ENDS in a credential word
// (`api_key`, `token`, `secret`, `password`, `bearer`, `authorization`, `client_key`), a subscript
// or `.get()`/`getenv()` naming a credential key (`headers['x-api-key']`,
// `request.headers.get("Authorization")`), each optionally sliced (`[:10]`, `.slice(0, 8)`) or
// re-encoded (`.strip()`, `.decode()`, `.toString()`).
// Presence, length and comparison pass (`len(api_key)`, `bool(token)`, `api_key is not None`,
// `token.length`), as do names that merely START with a credential word (`secretError`,
// `token_count`, `tokenizer`) and anything outside a log call.
// A line that must print one on purpose (a CI `::add-mask::` directive, which HIDES it) says so:
// `secret-log-ok: <why>` anywhere in the call.
// Not seen: a credential passed through an intermediate variable with an innocent name, a whole
// headers/config object logged as one value, and a call whose arguments span more than
// MAX_CONTINUATION extra lines (scanned as far as it reaches).
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const MAX_CONTINUATION = 4;
const LOG_CALL = /\b(?:logger|log|logging|console|_log|LOG)\.(?:debug|info|warn|warning|error|exception|critical|fatal|log|trace)\s*\(|(?<![\w$.])print\s*\(|\blog_event\s*\(/g;

const WORD = String.raw`(?:api_?key|apikey|token|secret|password|passwd|bearer|authorization|client_?key)`;
const KEY = String.raw`[\w-]*(?:api[-_]?key|token|secret|password|passwd|bearer|authorization|client[-_]?key)`;
const PATH = String.raw`[A-Za-z_$][\w$]*(?:\s*(?:\??\.\s*[A-Za-z_$][\w$]*|\[[^\]]*\]|\([^()]*\)))*`;
const TAIL = String.raw`(?:\s*(?:\[[^\]]*\]|\??\.\s*(?:slice|substring|substr|strip|lstrip|rstrip|decode|encode|lower|upper|trim|toString|hex)\([^)]*\)))*`;
const CRED_REFS = [
  // api_key, self.api_key, req.apiKey, process.env.API_KEY — sliced or not
  new RegExp(String.raw`^(?:${PATH}\s*\??\.\s*)?[\w$]*${WORD}${TAIL}$`, "i"),
  // headers['x-api-key'], os.environ["API_KEY"]
  new RegExp(String.raw`^${PATH}\s*\??\.?\[\s*(['"\`])${KEY}\1\s*\]${TAIL}$`, "i"),
  // request.headers.get("Authorization"), os.getenv("OPENAI_API_KEY")
  new RegExp(String.raw`^(?:${PATH}\s*\??\.\s*)?(?:get|getenv|getHeader|get_header)\(\s*(['"\`])${KEY}\1[^)]*\)${TAIL}$`, "i"),
];
const WRAPPER = /^(?:str|repr|String)\s*\(([\s\S]*)\)$/;

// Read a string literal starting at i (text[i] is the quote). Returns { end, interps }.
function readString(text, i) {
  const q = text[i];
  let prefix = "";
  for (let k = i - 1; k >= 0 && /[rRbBuUfF]/.test(text[k]) && prefix.length < 2; k--) prefix = text[k] + prefix;
  if (i - prefix.length - 1 >= 0 && /[\w$]/.test(text[i - prefix.length - 1])) prefix = "";
  const fstr = q === "`" || /f/i.test(prefix);
  const triple = q !== "`" && text.startsWith(q.repeat(3), i);
  const close = triple ? q.repeat(3) : q;
  const interps = [];
  let j = i + close.length;
  while (j < text.length) {
    const c = text[j];
    if (c === "\\") { j += 2; continue; }
    if (text.startsWith(close, j)) return { end: j + close.length, interps };
    const open = q === "`" ? (c === "$" && text[j + 1] === "{" ? 2 : 0) : (fstr && c === "{" ? (text[j + 1] === "{" ? -1 : 1) : 0);
    if (open === -1) { j += 2; continue; }
    if (open > 0) {
      const start = j + open;
      const end = matchClose(text, start, "{", "}");
      interps.push({ expr: text.slice(start, end), fstring: q !== "`" });
      j = end + 1;
      continue;
    }
    if (!triple && q !== "`" && c === "\n") return { end: j, interps };
    j++;
  }
  return { end: text.length, interps };
}

// Index of the bracket closing the one opened just before `start` (strings skipped).
function matchClose(text, start, open, close) {
  let depth = 1;
  for (let j = start; j < text.length; j++) {
    const c = text[j];
    if (c === "'" || c === '"' || c === "`") { j = readString(text, j).end - 1; continue; }
    if (c === open || c === "(" || c === "[" || (c === "{" && open !== "{")) depth++;
    else if (c === close || c === ")" || c === "]" || (c === "}" && close !== "}")) { if (--depth === 0) return j; }
  }
  return text.length;
}

// Split `text` on separators at bracket depth 0, outside strings; collect every interpolation seen.
function splitTop(text, seps, interps = []) {
  const parts = [];
  let depth = 0, from = 0;
  for (let j = 0; j < text.length; j++) {
    const c = text[j];
    if (c === "'" || c === '"' || c === "`") {
      const s = readString(text, j);
      interps.push(...s.interps);
      j = s.end - 1;
      continue;
    }
    if ("([{".includes(c)) depth++;
    else if (")]}".includes(c)) depth--;
    else if (depth === 0) {
      const sep = seps.find((s) => (s instanceof RegExp ? s.test(text.slice(j)) : text.startsWith(s, j)));
      if (sep !== undefined) {
        const len = sep instanceof RegExp ? text.slice(j).match(sep)[0].length : sep.length;
        parts.push(text.slice(from, j));
        from = j + len;
        j += len - 1;
      }
    }
  }
  parts.push(text.slice(from));
  return parts;
}

const OPERATORS = ["||", "??", /^\s+or\s+/, /^\+(?![+=])/, /^%(?!=)/];

// Does this expression put a credential's value into the output?
function leaks(expr, fstring = false) {
  let e = expr.trim();
  const kw = e.match(/^[A-Za-z_]\w*\s*=(?!=)\s*([\s\S]*)$/);
  if (kw) e = kw[1].trim();
  if (fstring) e = splitTop(e, [/^![rsa](?=\s*(?::|$))/, /^:(?!=)/])[0].trim();
  const operands = splitTop(e, OPERATORS);
  for (const raw of operands) {
    const op = raw.trim();
    if (!op) continue;
    if (CRED_REFS.some((re) => re.test(op))) return true;
    const wrapped = op.match(WRAPPER);
    if (wrapped && leaks(wrapped[1])) return true;
    if (op.startsWith("(") && matchClose(op, 1, "(", ")") === op.length - 1 && splitTop(op.slice(1, -1), [","]).some((a) => leaks(a))) return true;
    const fmt = op.match(/^[rRbBuU]?(['"])[\s\S]*?\1\s*\.format\s*\(/);
    if (fmt) {
      const open = fmt[0].length;
      if (splitTop(op.slice(open, matchClose(op, open, "(", ")")), [","]).some((a) => leaks(a))) return true;
    }
  }
  return false;
}

// Every interpolation inside an expression, nested strings included.
function interpolations(text) {
  const out = [];
  splitTop(text, [], out);
  for (let k = 0; k < out.length; k++) splitTop(out[k].expr, [], out);
  return out;
}

// A call named in a comment or quoted as `code` in a docstring is prose, not a call.
const prose = (before) => /^\s*(?:#|\/\/|\*)/.test(before) || /`$/.test(before) || /(?:^|\s)(?:#|\/\/)\s/.test(before);

export function scanSource(path, text) {
  const hits = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const m of line.matchAll(LOG_CALL)) {
      if (prose(line.slice(0, m.index))) continue;
      const window = lines.slice(i, i + 1 + MAX_CONTINUATION).join("\n");
      const open = m.index + m[0].length;
      const close = matchClose(window, open, "(", ")");
      const call = window.slice(open, close);
      if (call.includes("secret-log-ok:") || window.slice(close, window.indexOf("\n", close) >>> 0).includes("secret-log-ok:")) continue;
      const leak = splitTop(call, [","]).some((a) => leaks(a)) || interpolations(call).some((p) => leaks(p.expr, p.fstring));
      if (leak) { hits.push(`${path}:${i + 1}: ${line.trim().slice(0, 160)}`); break; }
    }
  });
  return hits;
}

const EXT = /\.(py|ts|tsx|js|mjs)$/;
// Tests assert on fixtures; the retired dashboard is outside the gates (services/.gateignore).
const SKIP = /(^|\/)(node_modules|tests?|__tests__|fixtures|eval)\/|\.test\.|_test\.py$|(^|\/)test_[^/]*\.py$|^services\/dashboard\//;

export function checkSecretLogs(root) {
  const files = execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8" })
    .split("\n").filter((f) => EXT.test(f) && !SKIP.test(f));
  return files.flatMap((f) => {
    let text;
    try { text = readFileSync(join(root, f), "utf8"); } catch { return []; }
    return scanSource(f, text);
  });
}
