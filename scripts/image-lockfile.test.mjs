// The meeting bot image installs exactly the packages pnpm-lock.yaml names: `pnpm install
// --frozen-lockfile`, never a resolve at build time. The bot carries native modules (the LiveKit
// FFI bindings among them), so what the image runs is what the lockfile pins.
// Run: node --test scripts/image-lockfile.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { guardTree } from "./test-tree.mjs";

guardTree();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const FROZEN = ["core/meetings/services/bot/Dockerfile"];

for (const rel of FROZEN) {
  test(`${rel} installs from the lockfile as it is`, () => {
    const src = readFileSync(join(ROOT, rel), "utf8");
    const installs = src.split("\n").filter((l) => /\bpnpm install\b/.test(l) && !l.trim().startsWith("#"));
    assert.ok(installs.length > 0, `${rel} has no pnpm install`);
    for (const line of installs) {
      assert.match(line, /--frozen-lockfile(\s|$)/, `${rel}: ${line.trim()}`);
      assert.doesNotMatch(line, /--frozen-lockfile=false|--no-frozen-lockfile/, `${rel}: ${line.trim()}`);
    }
  });
}
