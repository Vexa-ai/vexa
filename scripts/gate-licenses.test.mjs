// gate:licenses reads `pnpm licenses list --json` and judges each licence's package list
// (Vexa-ai/vexa#1906). When pnpm cannot read the dependency tree it prints an error object, and an
// entry that is not a package list used to crash the gate on `pkgs.map is not a function`. These
// cases run the shipped gate as a subprocess (it is a CLI with no exports) with a fake `pnpm` first on
// PATH, in a minimal tree, and pin both halves: a malformed or error report is a readable failure with
// the `pnpm install` hint, and a well-formed report is judged exactly as before.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const GATES = join(dirname(fileURLToPath(import.meta.url)), "gates.mjs");

// Run `node scripts/gates.mjs licenses` with a fake pnpm that prints `stdout` and exits `code`.
// The working tree is a minimal git repo with an empty Python licence index, so only the npm half
// has anything to judge: no lockfiles, no Dockerfiles, no license-exceptions.json.
function runGate(stdout, code = 0) {
  const dir = mkdtempSync(join(tmpdir(), "gate-licenses-"));
  try {
    const bin = join(dir, "bin");
    const cwd = join(dir, "tree");
    mkdirSync(bin);
    mkdirSync(cwd);
    writeFileSync(join(dir, "report.json"), stdout);
    const pnpm = join(bin, "pnpm");
    writeFileSync(pnpm, `#!/bin/sh\ncat '${join(dir, "report.json")}'\nexit ${code}\n`);
    chmodSync(pnpm, 0o755);
    writeFileSync(join(cwd, "python-licenses.json"), JSON.stringify({ licenses: {} }));
    spawnSync("git", ["init", "-q"], { cwd });
    const r = spawnSync(process.execPath, [GATES, "licenses"], {
      cwd,
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}` },
      encoding: "utf8",
    });
    return { status: r.status, out: `${r.stdout}\n${r.stderr}` };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const PNPM_ERROR = JSON.stringify({
  error: {
    code: "ERR_PNPM_MISSING_PACKAGE_INDEX_FILE",
    message: "Failed to find package index file for vitest@4.1.9 please consider running pnpm install",
  },
});

test("licenses: pnpm error object → readable refusal with pnpm's own message, no TypeError", () => {
  const { status, out } = runGate(PNPM_ERROR, 1);
  assert.equal(status, 1, out);
  assert.match(out, /✗ .*ERR_PNPM_MISSING_PACKAGE_INDEX_FILE/, out);
  assert.match(out, /Failed to find package index file for vitest@4\.1\.9/, out);
  assert.match(out, /run `pnpm install` first/, out);
  assert.doesNotMatch(out, /TypeError/, out);
});

test("licenses: a licence entry that is not a package list fails by name, no TypeError", () => {
  const { status, out } = runGate(JSON.stringify({ MIT: [{ name: "a" }], Weird: { x: 1 } }));
  assert.equal(status, 1, out);
  assert.match(out, /✗ licence entry "Weird" .*not a package list/, out);
  assert.match(out, /run `pnpm install` first/, out);
  assert.doesNotMatch(out, /TypeError/, out);
});

test("licenses: a report that is not an object (array, null) fails as an unexpected shape", () => {
  for (const report of ["[]", "null", "42"]) {
    const { status, out } = runGate(report);
    assert.equal(status, 1, `${report}: ${out}`);
    assert.match(out, /✗ `pnpm licenses list --json` failed: unexpected output shape — run `pnpm install` first/, `${report}: ${out}`);
    assert.doesNotMatch(out, /TypeError/, `${report}: ${out}`);
  }
});

test("licenses: a clean report is green with the same count", () => {
  const { status, out } = runGate(JSON.stringify({ MIT: [{ name: "a" }, { name: "b" }], ISC: [{ name: "c" }] }));
  assert.equal(status, 0, out);
  assert.match(out, /✓ gate:licenses — 3 npm deps OSS-clean/, out);
});

test("licenses: a Cat X report gets the same FORBIDDEN verdict", () => {
  const { status, out } = runGate(JSON.stringify({ "GPL-3.0": [{ name: "g" }] }));
  assert.equal(status, 1, out);
  assert.match(out, /✗ FORBIDDEN \(Cat X\) GPL-3\.0: g — replace this dependency/, out);
});
