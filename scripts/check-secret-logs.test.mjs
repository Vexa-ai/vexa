// Tests OF the secret-logs checker — the instrument gate:secret-logs trusts (#1868).
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkSecretLogs, scanSource } from "./check-secret-logs.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("a log line carrying a credential or a slice of one is caught", () => {
  for (const line of [
    'logger.info(f"   Token preview: {api_key[:10]}... (length: {len(api_key)})")',  // the 2026-10-23 leak
    "console.log(`key ${apiKey}`)",
    'log.warning(f"secret={webhook_secret}")',
    "console.error(`t ${token.slice(0, 8)}`)",
    'logger.debug(f"bearer {client_key!r}")',
  ]) assert.equal(scanSource("x.py", line).length, 1, line);
});

test("presence, length, other names and non-log lines pass", () => {
  for (const line of [
    'logger.info(f"length {len(api_key)}")',
    'logger.info(f"X-API-Key present: {api_key is not None}")',
    "console.error(`[terminal] refusing to start: ${secretError}`)",
    'logger.info(f"{token_count} tokens")',
    'headers = {"X-API-Key": f"{api_key}"}',
    "console.log(`::add-mask::${accessToken}`);  // secret-log-ok: hides it from the CI log",
  ]) assert.deepEqual(scanSource("x.py", line), [], line);
});

test("this repository logs no credential", () => {
  assert.deepEqual(checkSecretLogs(ROOT), []);
});
