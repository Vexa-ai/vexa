// Regression test for the error-text swallow in gates.mjs (#1107).
//
// The bug was not in a gate's logic but in how every gate REPORTED its own failure:
// `(e.stdout || e.stderr || e).toString()`. Under `stdio: "pipe"`, a child that writes
// only to stderr leaves `e.stdout` a zero-length Buffer — an object, therefore TRUTHY —
// so the `||` chain short-circuits on it and returns "". The operator saw the gate's name,
// a colon, and nothing at all, with `--no-verify` the only move left.
//
// This test drives a REAL execSync failure rather than a hand-built object, because the
// defect lives in what Node actually attaches to the thrown error. A test that asserted
// against `{stdout: Buffer.alloc(0), stderr: Buffer.from("boom")}` would pass against a
// wrong implementation that happened to check `!= null` instead of `.length`.
//
// Run: node --test scripts/gate-error-text.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// The implementation under test, lifted from gates.mjs by source so the test cannot drift
// from it silently: if the helper is edited, this reads the edited version.
function loadErrText() {
  const src = readFileSync(join(ROOT, "scripts", "gates.mjs"), "utf8");
  const m = src.match(/const errText = \(e\) => \{[\s\S]*?\n\};/);
  assert(m, "gates.mjs no longer defines errText — the swallow guard has been removed");
  return new Function(`${m[0]}; return errText;`)();
}

function realFailure(script) {
  try {
    execSync(`node -e ${JSON.stringify(script)}`, { stdio: "pipe" });
    assert.fail("expected the child to exit non-zero");
  } catch (e) {
    return e;
  }
}

test("stderr-only failure: the message survives (this is the bug)", () => {
  const errText = loadErrText();
  const e = realFailure('process.stderr.write("BOOM-stderr"); process.exit(1)');

  // The precondition that made the old code wrong — assert it, so the test still means
  // something if Node ever changes what it attaches.
  assert.equal(e.stdout.length, 0, "precondition: stdout is empty");
  assert(Boolean(e.stdout), "precondition: an empty Buffer is truthy — this is why || failed");
  assert.equal((e.stdout || e.stderr || e).toString(), "", "the old expression returns nothing");

  assert.match(errText(e), /BOOM-stderr/);
});

test("stdout-only failure: stdout is preferred", () => {
  const errText = loadErrText();
  const e = realFailure('process.stdout.write("BOOM-stdout"); process.exit(1)');
  assert.match(errText(e), /BOOM-stdout/);
});

test("both streams: stdout wins, and nothing throws", () => {
  const errText = loadErrText();
  const e = realFailure('process.stdout.write("OUT"); process.stderr.write("ERR"); process.exit(1)');
  assert.match(errText(e), /OUT/);
});

test("neither stream (spawn failure): falls back to the error itself, never empty", () => {
  const errText = loadErrText();
  let e;
  try {
    execSync("definitely-not-a-real-binary-1107", { stdio: "pipe" });
  } catch (err) {
    e = err;
  }
  assert(errText(e).length > 0, "a failure must never report an empty message");
});

test("no swallow sites remain in gates.mjs", () => {
  const src = readFileSync(join(ROOT, "scripts", "gates.mjs"), "utf8");
  assert.equal(
    (src.match(/e\.stdout \|\| e\.stderr/g) || []).length, 0,
    "a gate is back to `e.stdout || e.stderr` and will report empty failures again",
  );
});
