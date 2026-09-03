import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  FLOWS_IMAGE,
  FLOWS_REQUIRED_FROM,
  PROD_DEPLOYED_IMAGES,
  REQUIRED_IMAGES,
  requiredImagesFor,
  BUILD_MATRIX_BY_IMAGE,
  RUNTIME_INPUTS_BY_IMAGE,
  assertNoRuntimeInputDrift,
  candidateBuildPlan,
  candidateBuildPlanFromChangedImages,
  candidateInputDrift,
  validateCandidateMap,
} from "./candidate-image-map.mjs";

const digest = (n) => `sha256:${n.repeat(64)}`;

function validMap(release = "v0.12.18") {
  return {
    schema_version: 1,
    release,
    stable_tag: release,
    candidate_tag: `${release}-260723.stage2`,
    build_source: "1".repeat(40),
    validation_source: "2".repeat(40),
    build_run: "https://github.com/Vexa-ai/vexa/actions/runs/30033899550",
    validation_run: "https://github.com/Vexa-ai/vexa/actions/runs/30036135103",
    images: Object.fromEntries(requiredImagesFor(release).map((image, index) => [
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

test("v0.12.23 bootstrap packet freezes build identity without claiming validation", () => {
  const raw = readFileSync(
    new URL("../releases/v0.12.23/candidate-images.bootstrap.json", import.meta.url),
  );
  assert.equal(
    createHash("sha256").update(raw).digest("hex"),
    "a993d466d3aa083dafde3e882375b70d1e1c95120af5f2dc542c856c8e80039f",
  );
  const map = JSON.parse(raw);
  assert.equal(map.packet_state, "bootstrap");
  assert.equal(map.release, "v0.12.23");
  assert.equal(map.stable_tag, "v0.12.23");
  assert.equal(map.candidate_tag, "v0.12.23-rc.10");
  assert.equal(map.validation_source, undefined);
  assert.equal(map.validation_run, undefined);
  assert.equal(Object.keys(map.images).length, 10);
  assert.equal(
    Object.values(map.images).reduce(
      (count, image) => count + Object.keys(image.platform_manifests).length,
      0,
    ),
    19,
  );
});

test("v0.12.23 canonical packet freezes the rc.21 train candidate", () => {
  const raw = readFileSync(
    new URL("../releases/v0.12.23/candidate-images.json", import.meta.url),
  );
  assert.equal(
    createHash("sha256").update(raw).digest("hex"),
    "c5a310465b9005573c9fef79534e34a7822447d6c7004f24271ed59df61fe2a6",
  );
  const map = validateCandidateMap(JSON.parse(raw), "v0.12.23");
  assert.equal(map.candidate_tag, "v0.12.23-rc.21");
  assert.equal(map.build_source, "2dec308224155586f24906a7a4d6241ff18392cb");
  assert.equal(
    map.build_run,
    "https://github.com/Vexa-ai/vexa/actions/runs/32185449851",
  );
  assert.equal(
    map.validation_run,
    "https://github.com/Vexa-ai/vexa/actions/runs/32188980106",
  );
  assert.equal(
    map.images["vexaai/vexa-bot"].digest,
    "sha256:2bd879c61cb24f3e20d698ded475f565ebfb4f07f2bf842d1ebf0299e9814314",
  );
  assert.equal(
    map.images["vexaai/vexa-lite"].digest,
    "sha256:bb382b2031c4040c3121c421580d3af7d9c6a94581079d30b01d2622586df524",
  );
  assert.equal(Object.keys(map.images).length, 10);
  assert.equal(
    Object.values(map.images).reduce(
      (count, image) => count + Object.keys(image.platform_manifests).length,
      0,
    ),
    19,
  );
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
    FLOWS_IMAGE,
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
    // the gateway joined the root-context builds when the edge stopped owning its route table:
    // it now COPYs each domain's routes.v1.json, which a service-scoped context cannot reach.
    "vexaai/v012-gateway: .dockerignore",
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
  assert.deepEqual(plan.build_matrix.map(({ repository }) => repository), [
    "vexa-lite",
  ]);
  assert.equal(plan.build_matrix[0].use_registry_cache, false);
  assert.equal(JSON.stringify(plan.build_matrix).includes("vexaai"), false);
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
    /Candidate provenance compares against the witnessed build commit[\s\S]*fetch-depth: 0/,
  );
  assert.match(
    workflow,
    /needs\.preflight\.outputs\.build_bot == 'true'/,
  );
  assert.match(
    workflow,
    /needs\.preflight\.outputs\.build_mode == 'bot-lite-delta'/,
  );
  assert.match(
    workflow,
    /node release\/dockerhub-tag-audit\.mjs[\s\S]*--target "\$VERSION"/,
  );
  assert.match(
    workflow,
    /RELEASE="v\$\{VERSION#v\}"[\s\S]*RELEASE="\$\{RELEASE%%-\*\}"[\s\S]*--arg release "\$RELEASE"/,
  );
  assert.doesNotMatch(workflow, /--arg release "v0\.12\.18"/);
  assert.doesNotMatch(
    workflow.match(/outputs:[\s\S]*?steps:\n/)?.[0] ?? "",
    /changed_images/,
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

test("a release with no prior candidate map retains the full image plan", () => {
  const plan = candidateBuildPlan(null);
  assert.equal(plan.mode, "full");
  assert.equal(plan.changed_images.length, REQUIRED_IMAGES.length);
  assert.equal(plan.build_matrix.length, REQUIRED_IMAGES.length - 1);
  assert.ok(plan.build_matrix.every(({ use_registry_cache }) => use_registry_cache));
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

test("v0.12.25 canonical packet binds the rc.1 train candidate", () => {
  const raw = readFileSync(
    new URL("../releases/v0.12.25/candidate-images.json", import.meta.url),
  );
  assert.equal(
    createHash("sha256").update(raw).digest("hex"),
    "d9d0cdd13623cb1a9b3772adff271b2b1b0aa457c11a7286cfb13655d813b289",
  );
  const map = validateCandidateMap(JSON.parse(raw), "v0.12.25");
  assert.equal(map.candidate_tag, "v0.12.25-rc.1");
  assert.equal(map.build_source, "dedb017355aa5a2827b6c910b87d91c730b33963");
  assert.equal(
    map.build_run,
    "https://github.com/Vexa-ai/vexa/actions/runs/33303578205",
  );
  assert.equal(
    map.images["vexaai/vexa-bot"].digest,
    "sha256:65f6904b98abb110f591c5082f12319955723e2a6e2c777f26aac9709548f00a",
  );
});

test("v0.12.26 canonical packet binds the rc.1 train candidate", () => {
  const raw = readFileSync(
    new URL("../releases/v0.12.26/candidate-images.json", import.meta.url),
  );
  assert.equal(
    createHash("sha256").update(raw).digest("hex"),
    "91e5d99e0b1c801902a2da3b7f4f5b4f78a23339a30ca87311cee81f4639477f",
  );
  const map = validateCandidateMap(JSON.parse(raw), "v0.12.26");
  assert.equal(map.candidate_tag, "v0.12.26-rc.1");
  assert.equal(map.images["vexaai/vexa-bot"].digest, "sha256:d0d0444e04932a911866b8e9c4e6629a7830339bafb0c76117186479f62cbffa");
});

// ── vexaai/v012-flows — the Minutes product joins the release image set (2026-09-03) ───────────

test("the flows image is required from the floor release forward", () => {
  assert.equal(FLOWS_REQUIRED_FROM, "v0.12.27");
  assert.ok(REQUIRED_IMAGES.includes(FLOWS_IMAGE));
  assert.ok(requiredImagesFor("v0.12.27").includes(FLOWS_IMAGE));
  assert.ok(requiredImagesFor("v0.13.0").includes(FLOWS_IMAGE));
  assert.ok(requiredImagesFor("v1.0.0").includes(FLOWS_IMAGE));
  for (const older of ["v0.12.26", "v0.12.23", "v0.11.9"]) {
    assert.equal(requiredImagesFor(older).includes(FLOWS_IMAGE), false, older);
    assert.equal(requiredImagesFor(older).length, REQUIRED_IMAGES.length - 1, older);
  }
});

test("a post-floor candidate map must carry the flows image", () => {
  const doc = validMap("v0.12.27");
  assert.ok(FLOWS_IMAGE in doc.images);
  assert.equal(validateCandidateMap(doc, "v0.12.27").release, "v0.12.27");

  const without = validMap("v0.12.27");
  delete without.images[FLOWS_IMAGE];
  assert.throws(() => validateCandidateMap(without), /image set mismatch/);
});

test("a pre-floor candidate map must NOT carry the flows image", () => {
  // The frozen packets are sha256-pinned evidence; they carry ten images and must keep validating.
  const doc = validMap("v0.12.26");
  assert.equal(FLOWS_IMAGE in doc.images, false);
  assert.doesNotThrow(() => validateCandidateMap(doc, "v0.12.26"));

  const widened = validMap("v0.12.26");
  widened.images[FLOWS_IMAGE] = validMap("v0.12.27").images[FLOWS_IMAGE];
  assert.throws(() => validateCandidateMap(widened), /image set mismatch/);
});

test("flows is a multi-arch root-context build like its siblings", () => {
  const row = BUILD_MATRIX_BY_IMAGE[FLOWS_IMAGE];
  assert.deepEqual(row, {
    name: "flows",
    repository: "v012-flows",
    context: ".",
    dockerfile: "core/flows/Dockerfile",
  });
  // The build job hardcodes platforms: linux/amd64,linux/arm64 and fans out from this map, so
  // membership IS the multi-arch shape; and the map validator demands both platforms for every
  // image except the bot.
  assert.equal(PROD_DEPLOYED_IMAGES.has(FLOWS_IMAGE), false);
  const doc = validMap("v0.12.27");
  assert.deepEqual(doc.images[FLOWS_IMAGE].platforms, ["linux/amd64", "linux/arm64"]);
  assert.equal(doc.images[FLOWS_IMAGE].attestations, true);
  assert.deepEqual(
    RUNTIME_INPUTS_BY_IMAGE[FLOWS_IMAGE],
    [".dockerignore", "core/flows", "behavior"],
  );
});

test("a post-floor release with no prior map builds flows too", () => {
  const plan = candidateBuildPlan(null);
  assert.ok(plan.changed_images.includes(FLOWS_IMAGE));
  assert.deepEqual(
    plan.build_matrix.map(({ name }) => name).filter((n) => n === "flows"),
    ["flows"],
  );
  const full = candidateBuildPlanFromChangedImages(
    validMap("v0.12.27"),
    requiredImagesFor("v0.12.27"),
  );
  assert.equal(full.mode, "full");
  assert.ok(full.changed_images.includes(FLOWS_IMAGE));
});

test("a root .dockerignore change invalidates the flows candidate too", (t) => {
  const repo = mkdtempSync(join(tmpdir(), "candidate-map-flows-drift-"));
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

  const doc = validMap("v0.12.27");
  doc.build_source = buildSource;
  assert.deepEqual(candidateInputDrift(doc, head, repo), [
    "vexaai/v012-agent-worker: .dockerignore",
    "vexaai/v012-agent-api: .dockerignore",
    "vexaai/v012-meeting-api: .dockerignore",
    "vexaai/v012-gateway: .dockerignore",
    "vexaai/v012-flows: .dockerignore",
    "vexaai/vexa-bot: .dockerignore",
  ]);
});

test("release-validate publishes and probes the flows image", () => {
  const workflow = readFileSync(
    new URL("../.github/workflows/release-validate.yml", import.meta.url),
    "utf8",
  );
  // ALL_IMAGES is the promote/identity fan-out and its comment demands lockstep with the build
  // set; every image there must also carry an identity assertion, or the `*)` arm fails the job.
  assert.match(workflow, /ALL_IMAGES: >-[\s\S]*vexaai\/v012-flows/);
  assert.equal(
    (workflow.match(/vexaai\/v012-flows\)\s+probe/g) || []).length,
    2,
    "flows needs an identity probe on both the amd64 and arm64 legs",
  );
});
