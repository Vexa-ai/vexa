import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  PROD_DEPLOYED_IMAGES,
  REQUIRED_IMAGES,
  RUNTIME_INPUTS_BY_IMAGE,
  assertNoRuntimeInputDrift,
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

test("refuses any runtime-input drift", () => {
  assert.doesNotThrow(() => assertNoRuntimeInputDrift([]));
  assert.throws(
    () => assertNoRuntimeInputDrift(["core/runtime/src/runtime_kernel/api.py"]),
    /new candidate|runtime image inputs differ/,
  );
});
