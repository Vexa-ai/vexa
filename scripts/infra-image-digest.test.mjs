// Third-party infrastructure images are pinned by digest, tag kept for reading, in compose AND in
// the Helm chart — and the two run the same bytes.
// Run: node --test scripts/infra-image-digest.test.mjs   (CI: the gates.yml `static` job)
//
// A tag such as `postgres:17-alpine` moves whenever its publisher pushes; the digest is the bytes a
// release was validated on. Every literal image line that is not one of our own `vexaai/` images
// (built from this tree) must read `name:tag@sha256:<64 hex>`. An image both surfaces run must carry
// the same reference on both, or a Helm install validates against different bytes than compose.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PINNED = /^[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[a-f0-9]{64}$/;

/** Literal third-party `image: ref` lines in one file (no interpolation, not ours). */
function thirdPartyImages(rel) {
  return readFileSync(join(ROOT, rel), "utf8").split("\n")
    .map((line, i) => ({ where: `${rel}:${i + 1}`, ref: (line.match(/^\s+image:\s*["']?([^\s"']+)["']?\s*$/) || [])[1] }))
    .filter(({ ref }) => ref && !ref.includes("${") && !ref.includes("{{") && !ref.startsWith("vexaai/"));
}
const nameOf = (ref) => ref.split(/[:@]/)[0];

const COMPOSE = thirdPartyImages("deploy/compose/docker-compose.yml");
const HELM = thirdPartyImages("deploy/helm/charts/vexa/values.yaml");

test("the stack's infrastructure images are among the ones checked", () => {
  for (const name of ["postgres", "valkey/valkey", "versity/versitygw"])
    assert.ok(COMPOSE.some(({ ref }) => nameOf(ref) === name), `no literal ${name} image line in compose`);
  for (const name of ["postgres", "valkey/valkey"])
    assert.ok(HELM.some(({ ref }) => nameOf(ref) === name), `no literal ${name} image line in the Helm values`);
});

test("every third-party image in compose and the Helm values is pinned by digest and keeps its tag", () => {
  for (const { where, ref } of [...COMPOSE, ...HELM])
    assert.match(ref, PINNED, `${where} ${ref} is not name:tag@sha256:<digest>`);
});

test("an image compose and Helm both run is the same reference on both", () => {
  const compose = new Map(COMPOSE.map(({ ref }) => [nameOf(ref), ref]));
  for (const { where, ref } of HELM) {
    if (!compose.has(nameOf(ref))) continue;
    assert.equal(ref, compose.get(nameOf(ref)), `${where} runs ${ref}; compose runs ${compose.get(nameOf(ref))}`);
  }
});
