// Third-party images are pinned by digest, tag kept for reading, on every deploy surface — and one
// tag means one set of bytes wherever it appears.
// Run: node --test scripts/infra-image-digest.test.mjs   (CI: the gates.yml `static` job)
//
// A tag such as `postgres:17-alpine` moves whenever its publisher pushes; the digest is the bytes a
// release was validated on. The set checked is scripts/pinned-images.mjs, the list CVE scanning and
// gate:image-licenses read: every compose file and Helm chart under deploy/ (`${VAR:-default}` →
// the default), every Dockerfile's base, `docker run` and `*_IMAGE` variables in deploy/ scripts and
// Makefiles. Each ref must read `name:tag@sha256:<64 hex>`. A ref that is the same `name:tag` on two
// surfaces (compose and Helm, compose and Lite) must carry the same digest, or one surface validates
// against different bytes than the other.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { guardTree } from "./test-tree.mjs";
import { collectPinnedImages } from "./pinned-images.mjs";

guardTree();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PINNED = /^[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[a-f0-9]{64}$/;
const DIGEST = "@sha256:" + "a".repeat(64);

const PINS = collectPinnedImages(ROOT);
const REFS = [...PINS.entries()].flatMap(([name, { refs, sources }]) => [...refs].map((ref) => ({ name, ref, source: sources.get(ref) })));

/** Literal third-party `image: ref` lines in one file (`${VAR:-default}` → default; not ours). */
function thirdPartyImages(rel) {
  return readFileSync(join(ROOT, rel), "utf8").split("\n")
    .map((line, i) => ({
      where: `${rel}:${i + 1}`,
      ref: ((line.match(/^\s+image:\s*["']?([^\s"']+)["']?\s*$/) || [])[1] || "").replace(/\$\{[^:}]*:-([^}]+)\}/g, "$1"),
    }))
    .filter(({ ref }) => ref && !ref.includes("$") && !ref.includes("{{") && !ref.startsWith("vexaai/"));
}
const nameOf = (ref) => ref.split(/[:@]/)[0];

const COMPOSE = thirdPartyImages("deploy/compose/docker-compose.yml");
const HELM = thirdPartyImages("deploy/helm/charts/vexa/values.yaml");

test("the stack's infrastructure images are among the ones checked", () => {
  for (const name of ["postgres", "valkey/valkey", "versity/versitygw", "livekit/livekit-server", "searxng/searxng"])
    assert.ok(COMPOSE.some(({ ref }) => nameOf(ref) === name), `no ${name} image line in compose`);
  for (const name of ["postgres", "valkey/valkey"])
    assert.ok(HELM.some(({ ref }) => nameOf(ref) === name), `no literal ${name} image line in the Helm values`);
  // Lite's sidecars come from Makefile variables, the transcription unit from its own compose files.
  for (const name of ["fedirz/faster-whisper-server", "postgres", "versity/versitygw", "nginx"])
    assert.ok(PINS.has(name), `${name} is missing from scripts/pinned-images.mjs`);
});

test("the pin pattern takes a tagged digest and refuses a tag alone or a digest alone", () => {
  assert.match(`postgres:17-alpine${DIGEST}`, PINNED);
  assert.match(`ghcr.io/speaches-ai/speaches:0.8.3-cpu${DIGEST}`, PINNED);
  for (const ref of ["fedirz/faster-whisper-server:latest-cpu", "postgres:17-alpine", `searxng/searxng${DIGEST}`])
    assert.doesNotMatch(ref, PINNED, `${ref} passed as pinned`);
});

test("every third-party image a deploy surface runs is pinned by digest and keeps its tag", () => {
  const loose = REFS.filter(({ ref }) => !PINNED.test(ref)).map(({ ref, source }) => `${ref} (${source})`);
  assert.deepEqual(loose, [], "not name:tag@sha256:<digest>");
});

test("one tag is one digest on every surface that runs it", () => {
  const byTag = new Map();
  for (const { ref } of REFS) {
    const [tagged, digest] = ref.split("@");
    if (!digest) continue;
    if (!byTag.has(tagged)) byTag.set(tagged, new Set());
    byTag.get(tagged).add(digest);
  }
  const split = [...byTag].filter(([, d]) => d.size > 1).map(([t, d]) => `${t} → ${[...d].join(" | ")}`);
  assert.deepEqual(split, [], "a tag pinned to different bytes on different surfaces");
});

test("an image compose and Helm both run is the same reference on both", () => {
  const compose = new Map(COMPOSE.map(({ ref }) => [nameOf(ref), ref]));
  for (const { where, ref } of HELM) {
    if (!compose.has(nameOf(ref))) continue;
    assert.equal(ref, compose.get(nameOf(ref)), `${where} runs ${ref}; compose runs ${compose.get(nameOf(ref))}`);
  }
});
