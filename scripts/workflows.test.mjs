// Workflow properties that are not a gate's: which images a step runs.
// Run: node --test scripts/workflows.test.mjs
import test from "node:test";
import { guardTree } from "./test-tree.mjs";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WF = join(ROOT, ".github", "workflows");
const read = (f) => readFileSync(join(WF, f), "utf8");

guardTree();

// R6-30: an image a step runs (an action's `image:` input) is pinned by digest. setup-qemu-action runs
// binfmt privileged in a job that holds the registry token.
test("every image a workflow step names in `with: image:` is pinned by digest", () => {
  const unpinned = [];
  for (const f of readdirSync(WF).filter((n) => /\.ya?ml$/.test(n))) {
    for (const m of read(f).matchAll(/^\s+image:\s*["']?([^\s"'#]+)/gm)) {
      const ref = m[1];
      if (ref.includes("${{")) continue;                     // a matrix value, scanned not run
      if (!/@sha256:[0-9a-f]{64}$/.test(ref)) unpinned.push(`${f}: ${ref}`);
    }
  }
  assert.deepEqual(unpinned, []);
});
