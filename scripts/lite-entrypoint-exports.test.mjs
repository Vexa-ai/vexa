// gate:config-contract reads every key deploy/lite/entrypoint.sh exports, wherever the `export`
// stands (architecture pass 6, S74).
// Run: node --test scripts/lite-entrypoint-exports.test.mjs   (CI: the gates.yml `static` job)
//
// The entrypoint's exports reach every Lite program, so the gate holds each to a declaration (or to
// CONFIG_LITE_UNADOPTED, naming the program it serves). Its parser matched only `export` at column 0,
// so a key exported from a branch — indented under an `if`, after a `case` label, after `&&` — was
// invisible to it: a switch exported after a `case` label once shipped that way, undeclared. These
// rows plant each shape in this file's own copy of the tree (scripts/test-tree.mjs) and run the real
// gate over it.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { guardTree, sandboxTree } from "./test-tree.mjs";

guardTree();
const ROOT = sandboxTree();
const ENTRYPOINT = join(ROOT, "deploy", "lite", "entrypoint.sh");

function runGate() {
  try {
    return { green: true, out: execFileSync("node", [join(ROOT, "scripts", "gates.mjs"), "config-contract"],
      { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }) };
  } catch (e) {
    return { green: false, out: `${e.stdout || ""}${e.stderr || ""}` };
  }
}

function withPlanted(lines, fn) {
  const original = readFileSync(ENTRYPOINT, "utf8");
  // after the shebang, so every planted line is a real line of the script the gate reads
  const [shebang, ...rest] = original.split("\n");
  writeFileSync(ENTRYPOINT, [shebang, ...lines, ...rest].join("\n"));
  try { return fn(); } finally { writeFileSync(ENTRYPOINT, original); }
}

test("an export the gate must see is found wherever it stands, and named by line", () => {
  const planted = [
    "if true; then",
    "    export VEXA_ZZ_INDENTED=1",                     // line 3: indented in a branch
    "fi",
    'case "${X:-}" in',
    "    a) export VEXA_ZZ_CASE_ARM=1;;",                // line 6: after a case label
    "esac",
    "true && export VEXA_ZZ_AFTER_AND=1",               // line 8: after &&
    "# export VEXA_ZZ_IN_A_COMMENT=1",                   // a comment: not an export
  ];
  const r = withPlanted(planted, runGate);
  assert.equal(r.green, false, "the gate stayed green over three undeclared exports");
  assert.match(r.out, /entrypoint\.sh:3 exports VEXA_ZZ_INDENTED\b/);
  assert.match(r.out, /entrypoint\.sh:6 exports VEXA_ZZ_CASE_ARM\b/);
  assert.match(r.out, /entrypoint\.sh:8 exports VEXA_ZZ_AFTER_AND\b/);
  assert.doesNotMatch(r.out, /VEXA_ZZ_IN_A_COMMENT/);
});

test("the shipped entrypoint's exports are all declared", () => {
  const r = runGate();
  assert.equal(r.green, true, r.out);
});
