import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  PROD_DEPLOYED_IMAGES,
  REQUIRED_IMAGES,
  BUILD_MATRIX_BY_IMAGE,
  RUNTIME_INPUTS_BY_IMAGE,
  assertNoRuntimeInputDrift,
  candidateBuildPlan,
  candidateBuildPlanFromChangedImages,
  candidateInputDrift,
  validateCandidateMap,
} from "./candidate-image-map.mjs";

const digest = (n) => `sha256:${n.repeat(64)}`;

function validMap() {
  return {
    schema_version: 1,
    release: "v0.12.18",
    stable_tag: "v0.12.18",
    candidate_tag: "v0.12.18-260723.stage2",
    build_source: "1".repeat(40),
    validation_source: "2".repeat(40),
    build_run: "https://github.com/Vexa-ai/vexa/actions/runs/30033899550",
    validation_run: "https://github.com/Vexa-ai/vexa/actions/runs/30036135103",
    images: Object.fromEntries(REQUIRED_IMAGES.map((image, index) => [
      image,
      {
        class: PROD_DEPLOYED_IMAGES.has(image) ? "prod_deployed" : "oss_only",
        digest: digest(((index + 1) % 10).toString()),
        platforms: image === "vexaai/vexa-bot"
          ? ["linux/amd64"]
          : ["linux/amd64", "linux/arm64"],
        platform_manifests: Object.fromEntries(
          (image === "vexaai/vexa-bot"
            ? ["linux/amd64"]
            : ["linux/amd64", "linux/arm64"]).map((platform, platformIndex) => [
              platform,
              {
                manifest_digest: digest(((index + platformIndex + 2) % 10).toString()),
                config_digest: digest(((index + platformIndex + 4) % 10).toString()),
              },
            ]),
        ),
        attestations: image !== "vexaai/vexa-bot",
        evidence: "exact candidate validation receipt",
      },
    ])),
  };
}

test("accepts the exact candidate set", () => {
  assert.equal(validateCandidateMap(validMap(), "v0.12.18").release, "v0.12.18");
});

test("refuses a missing image", () => {
  const doc = validMap();
  delete doc.images["vexaai/v012-runtime"];
  assert.throws(() => validateCandidateMap(doc), /image set mismatch/);
});

test("refuses a truncated digest and platform overclaim", () => {
  const doc = validMap();
  doc.images["vexaai/vexa-bot"].digest = "sha256:1234";
  assert.throws(() => validateCandidateMap(doc), /invalid digest/);

  const second = validMap();
  second.images["vexaai/vexa-bot"].platforms.push("linux/arm64");
  assert.throws(() => validateCandidateMap(second), /platforms/);
});

test("refuses a class mismatch or incomplete platform identity", () => {
  const wrongClass = validMap();
  wrongClass.images["vexaai/v012-runtime"].class = "oss_only";
  assert.throws(() => validateCandidateMap(wrongClass), /class/);

  const missingPlatform = validMap();
  delete missingPlatform.images["vexaai/v012-runtime"].platform_manifests["linux/arm64"];
  assert.throws(() => validateCandidateMap(missingPlatform), /platform_manifests/);

  const invalidConfig = validMap();
  invalidConfig.images["vexaai/vexa-bot"]
    .platform_manifests["linux/amd64"].config_digest = "sha256:1234";
  assert.throws(() => validateCandidateMap(invalidConfig), /invalid config digest/);
});

test("requires a complete per-image candidate override", () => {
  const incomplete = validMap();
  incomplete.images["vexaai/vexa-bot"].candidate_tag = "v0.12.18-260724.stage3";
  assert.throws(() => validateCandidateMap(incomplete), /candidate override must define/);

  const complete = validMap();
  Object.assign(complete.images["vexaai/vexa-bot"], {
    candidate_tag: "v0.12.18-260724.stage3",
    build_source: "3".repeat(40),
    validation_source: "4".repeat(40),
    validation_run: "https://github.com/Vexa-ai/vexa/actions/runs/30070000000",
  });
  assert.doesNotThrow(() => validateCandidateMap(complete));
});

test("every root-context image tracks the ignore file that shapes its inputs", () => {
  for (const image of [
    "vexaai/v012-agent-worker",
    "vexaai/v012-agent-api",
    "vexaai/v012-meeting-api",
    "vexaai/vexa-bot",
  ]) {
    assert.ok(RUNTIME_INPUTS_BY_IMAGE[image].includes(".dockerignore"), image);
  }
  assert.ok(
    RUNTIME_INPUTS_BY_IMAGE["vexaai/vexa-lite"]
      .includes("deploy/lite"),
    "Lite input set carries Dockerfile.lite.dockerignore through deploy/lite",
  );
});

test("a root .dockerignore-only change invalidates every affected candidate", (t) => {
  const repo = mkdtempSync(join(tmpdir(), "candidate-map-drift-"));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  const git = (...args) => execFileSync("git", args, { cwd: repo, encoding: "utf8" }).trim();

  git("init", "--quiet");
  git("config", "user.name", "Candidate Map Test");
  git("config", "user.email", "candidate-map-test@vexa.invalid");
  writeFileSync(join(repo, ".dockerignore"), "node_modules\n");
  git("add", ".dockerignore");
  git("commit", "--quiet", "-m", "base");
  const buildSource = git("rev-parse", "HEAD");

  writeFileSync(join(repo, ".dockerignore"), "node_modules\n*.tmp\n");
  git("add", ".dockerignore");
  git("commit", "--quiet", "-m", "change build context");
  const head = git("rev-parse", "HEAD");

  const doc = validMap();
  doc.build_source = buildSource;
  assert.deepEqual(candidateInputDrift(doc, head, repo), [
    "vexaai/v012-agent-worker: .dockerignore",
    "vexaai/v012-agent-api: .dockerignore",
    "vexaai/v012-meeting-api: .dockerignore",
    "vexaai/vexa-bot: .dockerignore",
  ]);
});

test("the replacement build plan is bounded to Bot and Lite", () => {
  const doc = validMap();
  const plan = candidateBuildPlanFromChangedImages(doc, [
    "vexaai/vexa-bot",
    "vexaai/vexa-lite",
  ]);
  assert.equal(plan.mode, "bot-lite-delta");
  assert.deepEqual(plan.changed_images, [
    "vexaai/vexa-bot",
    "vexaai/vexa-lite",
  ]);
  assert.deepEqual(plan.build_matrix.map(({ image }) => image), [
    "vexaai/vexa-lite",
  ]);
  assert.equal(plan.build_bot, true);
  assert.equal(plan.base_candidate_tag, doc.candidate_tag);
});

test("release-images consumes the planner's dynamic matrix instead of a literal fan-out", () => {
  assert.deepEqual(
    Object.keys(BUILD_MATRIX_BY_IMAGE),
    REQUIRED_IMAGES.filter((image) => image !== "vexaai/vexa-bot"),
  );
  const workflow = readFileSync(
    new URL("../.github/workflows/release-images.yml", import.meta.url),
    "utf8",
  );
  assert.match(
    workflow,
    /include: \$\{\{ fromJSON\(needs\.preflight\.outputs\.build_matrix\) \}\}/,
  );
  assert.match(
    workflow,
    /needs\.preflight\.outputs\.build_bot == 'true'/,
  );
  assert.match(
    workflow,
    /needs\.preflight\.outputs\.build_mode == 'bot-lite-delta'/,
  );
});

test("a partial build cannot silently widen beyond the validated Bot+Lite path", () => {
  const doc = validMap();
  assert.throws(
    () => candidateBuildPlanFromChangedImages(doc, ["vexaai/vexa-bot"]),
    /unsupported partial candidate build/,
  );
  assert.throws(
    () => candidateBuildPlanFromChangedImages(doc, [
      "vexaai/vexa-bot",
      "vexaai/v012-runtime",
    ]),
    /unsupported partial candidate build/,
  );
});

test("a release with no prior candidate map retains the full ten-image plan", () => {
  const plan = candidateBuildPlan(null);
  assert.equal(plan.mode, "full");
  assert.equal(plan.changed_images.length, REQUIRED_IMAGES.length);
  assert.equal(plan.build_matrix.length, REQUIRED_IMAGES.length - 1);
  assert.equal(plan.build_bot, true);
  assert.equal(plan.base_candidate_tag, null);
});

test("refuses any runtime-input drift", () => {
  assert.doesNotThrow(() => assertNoRuntimeInputDrift([]));
  assert.throws(
    () => assertNoRuntimeInputDrift(["core/runtime/src/runtime_kernel/api.py"]),
    /new candidate|runtime image inputs differ/,
  );
});
