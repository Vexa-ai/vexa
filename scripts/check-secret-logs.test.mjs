// Tests OF the secret-logs checker — the instrument gate:secret-logs trusts (#1868).
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkSecretLogs, scanSource } from "./check-secret-logs.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const caught = (lines) => { for (const l of lines) assert.equal(scanSource("x.py", l).length, 1, l); };
const passes = (lines) => { for (const l of lines) assert.deepEqual(scanSource("x.py", l), [], l); };

test("an interpolated credential or a slice of one is caught", () => caught([
  'logger.info(f"   Token preview: {api_key[:10]}... (length: {len(api_key)})")',  // the 2026-10-23 leak
  "console.log(`key ${apiKey}`)",
  'log.warning(f"secret={webhook_secret}")',
  "console.error(`t ${token.slice(0, 8)}`)",
  'logger.debug(f"bearer {client_key!r}")',
  'logger.info(f"{api_key!r:>10}")',
]));

test("a credential passed as a positional or keyword argument is caught", () => caught([
  'logger.info("key %s", api_key)',
  'logger.info("key %s", api_key[:10])',
  'console.log("key", token)',
  'print("t", secret)',
  'print(str(token))',
  'log_event("auth", token=token)',
  'logger.info("pair %s %s", (user, password))',
]));

test("concatenation, %-formatting and .format() of a credential are caught", () => caught([
  'logger.info("key " + api_key)',
  "console.log('Bearer ' + token)",
  'logger.info("key %s" % api_key)',
  'logger.info("key {}".format(api_key[:8]))',
  "console.warn(`auth ${'Bearer ' + accessToken}`)",
]));

test("attribute access and credential-naming subscripts are caught", () => caught([
  'logger.info(f"{self.api_key}")',
  "console.log(`${req.apiKey}`)",
  'logger.info("%s", self.token)',
  "logger.info(f\"{headers['x-api-key']}\")",
  'logger.info("auth %s", headers["Authorization"])',
  'logger.info("key %s", request.headers.get("X-API-Key"))',
  "console.log('auth', req.headers['authorization'])",
  'logger.info("env %s", os.environ["OPENAI_API_KEY"])',
  'logger.info("env %s", os.getenv("OPENAI_API_KEY"))',
]));

test("a call whose arguments continue over the next lines is caught", () => {
  assert.equal(scanSource("x.py", 'logger.info(\n    "key %s",\n    api_key,\n)').length, 1);
  assert.equal(scanSource("x.ts", "console.log(\n  'auth',\n  'Bearer ' + token\n);").length, 1);
  assert.deepEqual(scanSource("x.py", 'logger.info(\n    "key %s",\n    api_key,\n)'), ["x.py:1: logger.info("]);
});

test("presence, length, other names, prose and non-log lines pass", () => passes([
  'logger.info(f"length {len(api_key)}")',
  'logger.info(f"X-API-Key present: {api_key is not None}")',
  'logger.info("length %d", len(api_key))',
  'logger.info("present %s", bool(token))',
  'print("unset", api_key is None)',
  "console.log(`length ${token.length}`)",
  "console.error(`[terminal] refusing to start: ${secretError}`)",
  'logger.info(f"{token_count} tokens")',
  'logger.info("model %s", tokenizer)',
  'logger.info("%s", data["token_count"])',
  'logger.info("not an f-string {token}")',
  'headers = {"X-API-Key": f"{api_key}"}',
  'headers["Authorization"] = "Bearer " + token',
  "    # never logger.info(token) here",
  "    `repr`/`str` are redacted so an accidental `logger.info(secret)` leaks nothing.",
  "console.log(`::add-mask::${accessToken}`);  // secret-log-ok: hides it from the CI log",
  'logger.info(\n    "key %s",  # secret-log-ok: a test fixture key\n    api_key,\n)',
]));

test("this repository logs no credential", () => {
  assert.deepEqual(checkSecretLogs(ROOT), []);
});
