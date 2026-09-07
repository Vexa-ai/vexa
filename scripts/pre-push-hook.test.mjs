// The pre-push hook's tool resolution and its failure text (Vexa-ai/vexa#1625).
// Run: node --test scripts/pre-push-hook.test.mjs
//
// THE DEFECT. Three workers hit this on 2026-09-06: `uv` installed in ~/.local/bin, a hook PATH
// that did not contain it, gate:contract-conformance dying on `/bin/sh: 1: uv: not found`, and the
// hook signing off with "or bypass with: git push --no-verify". An environment gap and a genuine
// contract break produced the SAME instruction, and the instruction skips both. A gate that teaches
// bypassing is worse than no gate: it trains the habit on the day nothing was actually wrong, and
// spends it on the day something is.
//
// WHY THESE TESTS CAN BE HERMETIC. The hook's search list is entirely $HOME-relative, so pointing
// HOME at a fixture determines the whole search — the assertions below are about the hook, not
// about what the test machine happens to have installed. The resolution block is lifted from the
// hook BY SOURCE (the same discipline as gate-error-text.test.mjs) so it cannot drift from the file
// that actually runs, and the end-to-end rows execute the real hook with stub `node`/`pnpm` so the
// gate loop costs nothing.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HOOK = join(ROOT, ".githooks", "pre-push");
const hookSource = () => readFileSync(HOOK, "utf8");

/** The PATH-widening block, exactly as the hook runs it. */
function resolutionBlock() {
  const m = hookSource().match(/# ── HOOK-TOOL-RESOLUTION-START[^\n]*\n([\s\S]*?)\n# ── HOOK-TOOL-RESOLUTION-END/);
  assert(m, "the hook no longer carries the HOOK-TOOL-RESOLUTION markers — this test is testing nothing");
  return m[1];
}

const SH = "/bin/sh";
/** A PATH with none of the tools under test on it. wc/tr/git (which the hook itself calls) live here. */
const BARE_PATH = "/usr/bin:/bin";

function tmpHome() {
  const home = mkdtempSync(join(tmpdir(), "vexa-1625-home-"));
  return home;
}
/** An executable stub at `path`, running `body`. */
function stub(path, body) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
  return path;
}
function sh(script, env) {
  try {
    return { code: 0, out: execFileSync(SH, ["-c", script], { encoding: "utf8", env, cwd: ROOT, stdio: "pipe" }) };
  } catch (e) {
    return { code: e.status ?? 1, out: `${e.stdout || ""}${e.stderr || ""}` };
  }
}

// ── the resolution itself, with a minimal PATH ──────────────────────────────────────────────────

for (const dir of [".local/bin", ".cargo/bin"]) {
  test(`a uv installed in ~/${dir} is found from a hook-shaped PATH`, () => {
    const home = tmpHome();
    try {
      stub(join(home, dir, "uv"), 'echo "planted uv"');
      const r = sh(`${resolutionBlock()}\ncommand -v uv || echo NOT-FOUND`, { PATH: BARE_PATH, HOME: home });
      assert.equal(r.out.trim(), join(home, dir, "uv"),
        `~/${dir} was not searched — this is the #1625 failure verbatim:\n${r.out}`);
    } finally { rmSync(home, { recursive: true, force: true }); }
  });
}

test("a uv the user already arranged on PATH is not shadowed by the fallback", () => {
  const home = tmpHome();
  const bin = mkdtempSync(join(tmpdir(), "vexa-1625-bin-"));
  try {
    stub(join(bin, "uv"), 'echo "chosen uv"');
    stub(join(home, ".local", "bin", "uv"), 'echo "fallback uv"');
    const r = sh(`${resolutionBlock()}\ncommand -v uv`, { PATH: `${bin}:${BARE_PATH}`, HOME: home });
    assert.equal(r.out.trim(), join(bin, "uv"),
      "the fallback dirs are appended, never prepended — a PATH the user arranged keeps winning");
  } finally { rmSync(home, { recursive: true, force: true }); rmSync(bin, { recursive: true, force: true }); }
});

// ── the whole hook, end to end ──────────────────────────────────────────────────────────────────

/** Runs the real hook with stub node/pnpm (so the 16-gate loop is instant) and a fixture HOME.
 *  `withUv` decides whether ~/.local/bin/uv exists — i.e. whether this is the #1625 environment. */
function runHook({ withUv }) {
  const home = tmpHome();
  const bin = mkdtempSync(join(tmpdir(), "vexa-1625-bin-"));
  const seenPath = join(bin, "node-path.txt");
  try {
    // the stub records the PATH the hook handed it: that is the observable proof the widening
    // happened before the gates ran, rather than the gates merely being skipped.
    stub(join(bin, "node"), `printf '%s' "$PATH" > ${JSON.stringify(seenPath)}\nexit 0`);
    stub(join(bin, "pnpm"), "exit 0");
    if (withUv) stub(join(home, ".local", "bin", "uv"), "exit 0");
    const r = sh(`${SH} ${JSON.stringify(HOOK)} origin git@github.com:Vexa-ai/vexa.git`,
      { PATH: `${bin}:${BARE_PATH}`, HOME: home });
    return { ...r, seenPath: existsSync(seenPath) ? readFileSync(seenPath, "utf8") : null, home };
  } finally { rmSync(home, { recursive: true, force: true }); rmSync(bin, { recursive: true, force: true }); }
}

test("uv reachable only from ~/.local/bin: the hook widens PATH and runs the gates", () => {
  const r = runHook({ withUv: true });
  assert.equal(r.code, 0, `the hook aborted although uv was installed under HOME:\n${r.out}`);
  assert(r.seenPath, "the gates were never invoked — the hook exited before its own loop");
  assert.match(r.seenPath, new RegExp(`${r.home.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/\\.local/bin`),
    "the gates ran on a PATH that still cannot see uv");
  assert.match(r.out, /static gates green/);
});

test("a genuinely missing uv is named, with how to install it — and never how to skip the hook", () => {
  const r = runHook({ withUv: false });
  assert.equal(r.code, 1, `a missing uv did not stop the push:\n${r.out}`);
  assert.match(r.out, /cannot resolve.*\buv\b/, `the message does not name the missing tool:\n${r.out}`);
  assert.match(r.out, /install uv/, `the message does not say how to install it:\n${r.out}`);
  assert.doesNotMatch(r.out, /--no-verify/,
    "the hook is back to teaching the bypass — an environment gap and a real gate break get the same instruction (#1625)");
  assert.equal(r.seenPath, null, "the hook ran gates it already knew could not work");
});

test("the hook file carries no bypass instruction at all", () => {
  assert.doesNotMatch(hookSource(), /--no-verify/,
    "a `--no-verify` string is back in the hook: the operator reads whichever line is in front of them (#1625)");
});
