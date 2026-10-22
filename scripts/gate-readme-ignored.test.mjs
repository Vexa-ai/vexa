// gate:readme (and every gate that walks directories) counts the tree, not the build output.
//
// The terminal build writes clients/terminal/public/mediapipe/{,wasm/}, gitignored, each with a
// generated README. The directory walk read the filesystem, so after any build gate:readme counted
// two more directories and arch-report's committed map went stale (567 → 569). This drives the real
// gates.mjs in a throwaway git repository: an ignored directory is not counted, a tracked one is.
//
// Run: node --test scripts/gate-readme-ignored.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { guardTree } from "./test-tree.mjs";

guardTree();

const GATES = join(dirname(fileURLToPath(import.meta.url)), "gates.mjs");
const readme = (cwd) => execFileSync("node", [GATES, "readme"], { cwd, stdio: ["ignore", "pipe", "pipe"] }).toString();
const write = (root, rel, text) => { mkdirSync(dirname(join(root, rel)), { recursive: true }); writeFileSync(join(root, rel), text); };

test("a gitignored directory is build output: gate:readme does not count it", () => {
  const root = mkdtempSync(join(tmpdir(), "gate-readme-"));
  try {
    execFileSync("git", ["init", "-q"], { cwd: root });
    write(root, "README.md", "root\n");
    write(root, "app/README.md", "app\n");
    write(root, "app/.gitignore", "/public/generated\n");
    write(root, "app/public/README.md", "public\n");
    assert.match(readme(root), /gate:readme — 3 dirs/);
    write(root, "app/public/generated/README.md", "generated\n");
    write(root, "app/public/generated/wasm/README.md", "generated\n");
    assert.match(readme(root), /gate:readme — 3 dirs/, "the ignored tree is not the repository's");
    write(root, "app/public/tracked/README.md", "tracked\n");
    assert.match(readme(root), /gate:readme — 4 dirs/, "a directory git does not ignore still counts");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
