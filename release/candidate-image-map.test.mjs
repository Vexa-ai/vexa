import assert from "node:assert/strict";
import test from "node:test";

import {
  PROD_DEPLOYED_IMAGES,
  REQUIRED_IMAGES,
  assertNoRuntimeInputDrift,
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

test("refuses any runtime-input drift", () => {
  assert.doesNotThrow(() => assertNoRuntimeInputDrift([]));
  assert.throws(
    () => assertNoRuntimeInputDrift(["core/runtime/src/runtime_kernel/api.py"]),
    /new candidate|runtime image inputs differ/,
  );
});
