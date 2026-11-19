import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";

import {
  LEGACY_DUAL_PUSH_ENDS_AFTER,
  SERVICE_IMAGES,
  UNPREFIXED_IMAGES,
  SOURCE_BUILT_IMAGES,
  dualPushes,
  legacyAliasFor,
  serviceOf,
} from "./image-names.mjs";
import {
  BUILD_MATRIX_BY_IMAGE,
  CURRENT_REQUIRED_IMAGES,
  candidateBuildPlanFromChangedImages,
} from "./candidate-image-map.mjs";
import { RELEASE_REPOSITORIES } from "./dockerhub-tag-audit.mjs";
import { guardTree } from "../scripts/test-tree.mjs";

guardTree();

const ROOT = new URL("..", import.meta.url).pathname;

// Where a `v012-` image name may still be written. Everything else derives from image-names.mjs
// or names `vexaai/vexa-<service>` directly. Historical release records are never rewritten.
const LEGACY_NAME_ALLOWED = [
  /^releases\/v0\.12\./,
  /^releases\/v0\.13\.[01]\//,
  /^release\/image-names(\.test)?\.mjs$/,
  /^release\/candidate-image-map(\.test)?\.mjs$/,        // schema 1-3 packets name the old images
  /^release\/release-validate-gating\.test\.mjs$/,
  /^scripts\/registry-candidate-validate\.test\.mjs$/,  // frozen v0.12 promotion fixtures
  /^\.github\/workflows\/release-images\.yml$/,         // documents and performs the transition push
  /^docs\/docs\/changelog\.mdx$/,                           // published history
  /^docs\/changelog\.d\/1783-image-names\.md$/,          // the rename's own note
  /^docs\/docs\/upgrade-image-names\.mdx$/,              // the upgrade note names both
  /^docs\/docs\/upgrade-from-0-12\.mdx$/,                // the upgrade guide names both
  /^docs\/adr\//,                                        // ADRs are dated records
  /^deploy\/transcription\//,                            // not a release image (built and pushed separately)
];

test("the dual push covers exactly one transition release", () => {
  assert.equal(LEGACY_DUAL_PUSH_ENDS_AFTER, "v0.13.3");
  assert.equal(dualPushes("v0.13.3"), true);
  assert.equal(dualPushes("v0.13.3-rc.2"), true);
  assert.equal(dualPushes("v0.13.4"), false);
  assert.equal(dualPushes("v0.14.0-rc.1"), false);
  assert.equal(legacyAliasFor("vexaai/vexa-mcp", "v0.13.3"), "v012-mcp");
  assert.equal(legacyAliasFor("vexaai/vexa-mcp", "v0.13.4"), "");
  assert.equal(legacyAliasFor("vexaai/vexa-bot", "v0.13.3"), "", "bot was never v012-");
  assert.equal(legacyAliasFor("vexaai/v012-mcp", "v0.13.3"), "", "a legacy name has no alias");
});

test("the current candidate set and build matrix use the vexa- names", () => {
  assert.deepEqual(
    [...CURRENT_REQUIRED_IMAGES].sort(),
    [...SERVICE_IMAGES, ...UNPREFIXED_IMAGES].sort(),
  );
  for (const image of SERVICE_IMAGES) {
    assert.equal(BUILD_MATRIX_BY_IMAGE[image].repository, image.replace("vexaai/", ""));
  }
  const plan = candidateBuildPlanFromChangedImages(null, CURRENT_REQUIRED_IMAGES);
  for (const row of plan.build_matrix) {
    if (serviceOf(`vexaai/${row.repository}`)) {
      assert.equal(row.legacy_repository, `v012-${serviceOf(`vexaai/${row.repository}`)}`);
    } else {
      assert.equal(row.legacy_repository, undefined);
    }
  }
});

test("the Docker Hub collision audit covers both names while the dual push lasts", () => {
  for (const image of SERVICE_IMAGES) {
    assert.ok(RELEASE_REPOSITORIES.includes(image.replace("vexaai/", "")), image);
    assert.ok(RELEASE_REPOSITORIES.includes(`v012-${serviceOf(image)}`), image);
  }
});

test("no surface outside the allowlist still writes a v012- image name", () => {
  const out = execFileSync("git", ["grep", "-n", "-I", "-E", "vexaai/v012-"], {
    cwd: ROOT, encoding: "utf8",
  }).trim();
  const offenders = out.split("\n").filter(Boolean)
    .filter((line) => !LEGACY_NAME_ALLOWED.some((re) => re.test(line.split(":")[0])));
  assert.deepEqual(offenders, [], "derive the name from release/image-names.mjs instead");
});

test("install paths never pull :latest of a release image", () => {
  const re = "vexaai/vexa-[a-z-]+:latest";
  let out = "";
  try {
    out = execFileSync("git", ["grep", "-n", "-I", "-E", re, "--", "deploy", "docs/docs"], {
      cwd: ROOT, encoding: "utf8",
    });
  } catch (e) { if (e.status !== 1) throw e; }
  const hits = out.split("\n").filter(Boolean)
    .filter((l) => SERVICE_IMAGES.some((i) => l.includes(`${i}:latest`)));
  assert.deepEqual(hits, []);
});

test("release-validate's ALL_IMAGES is the image-names list", async () => {
  const { readFileSync } = await import("node:fs");
  const yml = readFileSync(new URL("../.github/workflows/release-validate.yml", import.meta.url), "utf8");
  const block = /\n  ALL_IMAGES: >-\n((?:    \S+\n)+)/.exec(yml);
  assert.ok(block, "ALL_IMAGES block");
  assert.deepEqual(block[1].trim().split(/\s+/).sort(), [...SERVICE_IMAGES, ...UNPREFIXED_IMAGES].sort());
});

test("compose installs the checkout's own release, an immutable tag", async () => {
  const { readFileSync } = await import("node:fs");
  const env = readFileSync(new URL("../deploy/compose/.env.example", import.meta.url), "utf8");
  const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(/^IMAGE_TAG=(.*)$/m.exec(env)?.[1], `v${pkg.version}`);
});

// Positive parity: compose and the chart run exactly the image-names set — every service image is
// named, and nothing they name under vexaai/vexa-* is outside it.
for (const [surface, files] of [
  ["compose", ["deploy/compose/docker-compose.yml"]],
  ["helm", ["deploy/helm/charts/vexa/values.yaml", "deploy/helm/charts/vexa/templates/_helpers.tpl"]],
]) {
  test(`${surface} names exactly the image-names service images`, async () => {
    const { readFileSync } = await import("node:fs");
    const text = files.map((f) => readFileSync(new URL(`../${f}`, import.meta.url), "utf8")).join("\n");
    const named = new Set([...text.matchAll(/vexaai\/vexa-[a-z][a-z-]*[a-z]/g)].map((m) => m[0]));
    const known = new Set([...SERVICE_IMAGES, ...UNPREFIXED_IMAGES, ...SOURCE_BUILT_IMAGES]);
    assert.deepEqual([...named].filter((i) => !known.has(i)).sort(), [], `${surface} names an image image-names.mjs does not`);
    assert.deepEqual(SERVICE_IMAGES.filter((i) => !named.has(i)), [], `${surface} is missing a service image`);
  });
}
