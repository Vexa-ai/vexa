// check-secret-logs — no log line carries any part of a credential (#1868).
//
// A key, bearer, session or signing secret may be logged by its PRESENCE or LENGTH, never by its
// value or a slice of it: `vxa_tx_` is seven characters, so `api_key[:10]` printed three secret
// ones (transcription-gateway, 2026-10-23). A log call is flagged when it interpolates a variable
// whose name ENDS in a credential word (`api_key`, `token`, `secret`, `password`, `bearer`,
// `authorization`, `client_key`), alone or sliced. `len(api_key)`, `bool(token)`, `api_key is not
// None` and names that merely start with one (`secretError`, `token_count`) pass. A line that must
// print one on purpose (a CI `::add-mask::` directive, which HIDES it) says so: `secret-log-ok: <why>`.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const LOG_CALL = /\b(?:logger|log|logging|console|_log|LOG)\.(?:debug|info|warn|warning|error|exception|critical|log|trace)\s*\(|\bprint\s*\(|\blog_event\s*\(/;
const CRED = String.raw`[A-Za-z_]*(?:api_?key|apiKey|ApiKey|token|Token|secret|Secret|password|Password|bearer|Bearer|authorization|Authorization|client_key|clientKey)`;
// `{api_key}`, `{api_key[:10]}`, `${token.slice(0, 8)}`, `${apiKey}` — the value or a slice of it
const LEAK = new RegExp(String.raw`[{]\s*` + CRED + String.raw`\s*(?:\[[^\]]*\]|\.slice\([^)]*\)|\.substring\([^)]*\)|\.substr\([^)]*\))?\s*(?:![rs])?\s*[}]`);
const EXT = /\.(py|ts|tsx|js|mjs)$/;
// Tests assert on fixtures; the retired dashboard is outside the gates (services/.gateignore).
const SKIP = /(^|\/)(node_modules|tests?|__tests__|fixtures|eval)\/|\.test\.|_test\.py$|(^|\/)test_[^/]*\.py$|^services\/dashboard\//;

export function scanSource(path, text) {
  const hits = [];
  text.split("\n").forEach((line, i) => {
    if (LOG_CALL.test(line) && LEAK.test(line) && !line.includes("secret-log-ok:")) hits.push(`${path}:${i + 1}: ${line.trim().slice(0, 160)}`);
  });
  return hits;
}

export function checkSecretLogs(root) {
  const files = execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8" })
    .split("\n").filter((f) => EXT.test(f) && !SKIP.test(f));
  return files.flatMap((f) => {
    let text;
    try { text = readFileSync(join(root, f), "utf8"); } catch { return []; }
    return scanSource(f, text);
  });
}
