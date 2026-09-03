// Regression tests for the terminal brick's gate:isolation checker
// (clients/terminal/scripts/check-isolation.js), run through the real gate.
// Run: node --test scripts/check-isolation.test.mjs   (CI: the gates.yml `static` job runs
// scripts/*.test.mjs directly — scripts/ is not a workspace package, so `pnpm test` never
// reaches these files)
//
// The defect this file pins is a FALSE POSITIVE, which is the expensive direction for a gate: it
// blocks a push, it names a file and a specifier, and it is wrong. Twice in one day the checker
// read the words `from "…"` as an import where they were not one — once in a doc comment
// (`a link was clicked` / `this turn did not come from an arrival`), once in a regular-expression
// literal, which is code and survives the comment strip. So the fixtures below are planted as REAL
// FILES in the tree the gate actually walks, and the real gate is run as a subprocess: a test that
// fed a fixture string to an exported regex would prove the regex, not the gate, and the specifier
// scan is the whole of what is under test.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
// An existing directory, so nothing is left behind for gate:readme to find (an empty leftover dir
// is invisible to `git status` and very visible to that gate).
const PLANTED = "clients/terminal/src/ui-kit/zzPlantedIsolation.tsx";

// A specifier that is not a builtin, not relative, not the `@/*` alias, and not in the terminal's
// package.json — so if the scanner sees it at all, the gate must red.
const UNDECLARED = "zz-not-a-real-dep";

function withPlanted(body, fn) {
  const abs = join(ROOT, PLANTED);
  writeFileSync(abs, body);
  try { return fn(); } finally { rmSync(abs, { force: true }); }
}

function runIsolation() {
  try {
    return { green: true, out: execFileSync("node", ["scripts/gates.mjs", "isolation"], { cwd: ROOT, encoding: "utf8" }) };
  } catch (e) {
    return { green: false, out: `${e.stdout || ""}${e.stderr || ""}` };
  }
}

test("vacuity control: the committed tree is green (a red here invalidates every row below)", () => {
  const r = runIsolation();
  assert.equal(r.green, true, r.out);
});

test("RED: a real undeclared import IS reported (the gate still does its job)", () => {
  const r = withPlanted(`import thing from "${UNDECLARED}";\nexport const x = thing;\n`, runIsolation);
  assert.equal(r.green, false, r.out);
  assert.match(r.out, new RegExp(`zzPlantedIsolation\\.tsx → ${UNDECLARED}`));
});

test("RED: the side-effect form `import \"pkg\"` is reported too (it was invisible before)", () => {
  const r = withPlanted(`import "${UNDECLARED}";\nexport const x = 1;\n`, runIsolation);
  assert.equal(r.green, false, r.out);
  assert.match(r.out, new RegExp(`zzPlantedIsolation\\.tsx → ${UNDECLARED}`));
});

test("RED: a multi-line named clause is still read (the anchor did not break real imports)", () => {
  const r = withPlanted(`import {\n  a,\n  b,\n} from "${UNDECLARED}";\nexport const x = [a, b];\n`, runIsolation);
  assert.equal(r.green, false, r.out);
  assert.match(r.out, new RegExp(`zzPlantedIsolation\\.tsx → ${UNDECLARED}`));
});

test("GREEN: `from \"pkg\"` inside a STRING literal is not an import", () => {
  const r = withPlanted(
    `export const msg = 'the reader cannot tell from "${UNDECLARED}" which one it was';\n` +
    `export const other = "import x from \\"${UNDECLARED}\\"";\n`,
    runIsolation);
  assert.equal(r.green, true, r.out);
});

test("GREEN: `from \"pkg\"` inside a REGEX literal is not an import (the 2026-09-02 push failure)", () => {
  const r = withPlanted(
    `export const RE = /^\\s*import .* from ["']${UNDECLARED}["']/;\n` +
    `export const RE2 = /from\\s+["']${UNDECLARED}["']/g;\n`,
    runIsolation);
  assert.equal(r.green, true, r.out);
});

test("GREEN: `from \"pkg\"` inside a comment is not an import (the first occurrence, still pinned)", () => {
  const r = withPlanted(
    `/** a link was clicked, and this turn did not come from "${UNDECLARED}". */\n` +
    `// require("${UNDECLARED}") named in prose, not called\n` +
    `export const x = 1;\n`,
    runIsolation);
  assert.equal(r.green, true, r.out);
});

test("GREEN: a TypeScript import-TYPE query names a types package, not a runtime dep", () => {
  // `import("mdx/types").MDXContent` at ui-kit/MdxDoc.tsx:268 is the shape this protects: the same
  // characters as a dynamic import, a package that is never installed at runtime.
  const r = withPlanted(`export type T = { C: import("${UNDECLARED}").Thing };\n`, runIsolation);
  assert.equal(r.green, true, r.out);
});
