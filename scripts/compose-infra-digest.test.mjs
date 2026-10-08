// Third-party infrastructure images in the compose stack are pinned by digest, tag kept for reading.
// Run: node --test scripts/compose-infra-digest.test.mjs   (CI: the gates.yml `static` job)
//
// A tag such as `postgres:17-alpine` moves whenever its publisher pushes; the digest is the bytes a
// release was validated on. Every literal image line that is not one of our own `vexaai/` images
// (built from this tree) must read `name:tag@sha256:<64 hex>`.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const COMPOSE = readFileSync(join(ROOT, "deploy/compose/docker-compose.yml"), "utf8");

const literalThirdParty = COMPOSE.split("\n")
  .map((line, i) => ({ line: i + 1, ref: (line.match(/^\s+image:\s*(\S+)\s*$/) || [])[1] }))
  .filter(({ ref }) => ref && !ref.includes("${") && !ref.startsWith("vexaai/"));

test("the stack's infrastructure images are among the ones checked", () => {
  const names = literalThirdParty.map(({ ref }) => ref.split(/[:@]/)[0]);
  for (const name of ["postgres", "valkey/valkey", "versity/versitygw"]) {
    assert.ok(names.includes(name), `no literal ${name} image line in compose`);
  }
});

test("every third-party image in compose is pinned by digest and keeps its tag", () => {
  for (const { line, ref } of literalThirdParty) {
    assert.match(ref, /^[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[a-f0-9]{64}$/,
      `docker-compose.yml:${line} ${ref} is not name:tag@sha256:<digest>`);
  }
});
