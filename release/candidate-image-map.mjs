#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const REQUIRED_IMAGES = [
  "vexaai/v012-admin-api",
  "vexaai/v012-runtime",
  "vexaai/v012-agent-worker",
  "vexaai/v012-agent-api",
  "vexaai/v012-meeting-api",
  "vexaai/v012-gateway",
  "vexaai/v012-mcp",
  "vexaai/v012-terminal",
  "vexaai/vexa-bot",
  "vexaai/vexa-lite",
];

export const PROD_DEPLOYED_IMAGES = new Set([
  "vexaai/v012-admin-api",
  "vexaai/v012-runtime",
  "vexaai/v012-meeting-api",
  "vexaai/v012-gateway",
  "vexaai/vexa-bot",
]);

// The union of every path copied by the ten release Dockerfiles. If any path
// differs from the witnessed build source, those bytes are a new candidate and
// may not be relabelled as the witnessed release.
export const RUNTIME_INPUT_PATHS = [
  "core",
  "clients/terminal",
  "deploy/lite",
  "package.json",
  "pnpm-lock.yaml",
  "pnpm-workspace.yaml",
  "tsconfig.base.json",
  "turbo.json",
  "scripts",
  "licenses",
];

const SHA = /^[0-9a-f]{40}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const VERSION = /^v\d+\.\d+\.\d+$/;

const fail = (message) => {
  throw new Error(message);
};

export function validateCandidateMap(doc, expectedVersion) {
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) fail("map must be an object");
  if (doc.schema_version !== 1) fail("schema_version must be 1");
  if (!VERSION.test(doc.release)) fail(`invalid stable release: ${doc.release}`);
  if (expectedVersion && doc.release !== expectedVersion) {
    fail(`map release ${doc.release} does not match requested ${expectedVersion}`);
  }
  if (doc.stable_tag !== doc.release) fail("stable_tag must equal release");
  if (typeof doc.candidate_tag !== "string" || !doc.candidate_tag.startsWith(`${doc.release}-`)) {
    fail("candidate_tag must be a suffixed tag for this release");
  }
  if (!SHA.test(doc.build_source)) fail("build_source must be a full 40-hex SHA");
  if (!SHA.test(doc.validation_source)) fail("validation_source must be a full 40-hex SHA");
  for (const field of ["build_run", "validation_run"]) {
    if (!/^https:\/\/github\.com\/Vexa-ai\/vexa\/actions\/runs\/\d+$/.test(doc[field] || "")) {
      fail(`${field} must be an exact Vexa-ai/vexa Actions run URL`);
    }
  }
  if (!doc.images || typeof doc.images !== "object" || Array.isArray(doc.images)) {
    fail("images must be an object keyed by repository");
  }

  const actual = Object.keys(doc.images).sort();
  const required = [...REQUIRED_IMAGES].sort();
  if (actual.join("\n") !== required.join("\n")) {
    fail(`image set mismatch\nactual=${actual.join(",")}\nrequired=${required.join(",")}`);
  }

  for (const image of REQUIRED_IMAGES) {
    const row = doc.images[image];
    if (!row || typeof row !== "object") fail(`${image}: row missing`);
    const expectedClass = PROD_DEPLOYED_IMAGES.has(image) ? "prod_deployed" : "oss_only";
    if (row.class !== expectedClass) {
      fail(`${image}: class ${row.class} != ${expectedClass}`);
    }
    if (!DIGEST.test(row.digest || "")) fail(`${image}: invalid digest`);
    if (!Array.isArray(row.platforms)) fail(`${image}: platforms must be an array`);
    const platforms = [...new Set(row.platforms)].sort();
    const expected = image === "vexaai/vexa-bot"
      ? ["linux/amd64"]
      : ["linux/amd64", "linux/arm64"];
    if (platforms.join("\n") !== expected.join("\n")) {
      fail(`${image}: platforms ${platforms.join(",")} != ${expected.join(",")}`);
    }
    if (
      !row.platform_manifests ||
      typeof row.platform_manifests !== "object" ||
      Array.isArray(row.platform_manifests)
    ) {
      fail(`${image}: platform_manifests must be an object`);
    }
    const manifestPlatforms = Object.keys(row.platform_manifests).sort();
    if (manifestPlatforms.join("\n") !== expected.join("\n")) {
      fail(
        `${image}: platform_manifests ${manifestPlatforms.join(",")} != ${expected.join(",")}`,
      );
    }
    for (const platform of expected) {
      const identity = row.platform_manifests[platform];
      if (!identity || typeof identity !== "object") {
        fail(`${image}: platform identity missing for ${platform}`);
      }
      if (!DIGEST.test(identity.manifest_digest || "")) {
        fail(`${image}: invalid manifest digest for ${platform}`);
      }
      if (!DIGEST.test(identity.config_digest || "")) {
        fail(`${image}: invalid config digest for ${platform}`);
      }
    }
    if (image !== "vexaai/vexa-bot" && row.attestations !== true) {
      fail(`${image}: multi-platform image must record attestations=true`);
    }
    if (typeof row.evidence !== "string" || row.evidence.trim() === "") {
      fail(`${image}: evidence is required`);
    }
  }
  return doc;
}

export function assertNoRuntimeInputDrift(changedPaths) {
  if (changedPaths.length > 0) {
    fail(
      "runtime image inputs differ from the witnessed build source:\n" +
      changedPaths.map((path) => `  ${path}`).join("\n"),
    );
  }
}

export function runtimeInputDrift(buildSource, head = "HEAD", cwd = process.cwd()) {
  const output = execFileSync(
    "git",
    ["diff", "--name-only", `${buildSource}..${head}`, "--", ...RUNTIME_INPUT_PATHS],
    { cwd, encoding: "utf8" },
  );
  return output.split("\n").map((line) => line.trim()).filter(Boolean);
}

function loadMap(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function usage() {
  console.error(
    "usage: candidate-image-map.mjs " +
    "<check|emit-tsv|emit-platform-tsv|check-source-inputs> " +
    "<map.json> [expected-version|head]",
  );
  process.exit(2);
}

function main(argv) {
  const [command, path, arg] = argv;
  if (!command || !path) usage();
  const doc = validateCandidateMap(loadMap(path), command === "check" ? arg : undefined);

  if (command === "check") {
    console.log(`✓ ${doc.release}: exact ten-image candidate map is well formed`);
    return;
  }
  if (command === "emit-tsv") {
    for (const image of REQUIRED_IMAGES) {
      console.log(`${image}\t${doc.images[image].digest}`);
    }
    return;
  }
  if (command === "emit-platform-tsv") {
    for (const image of REQUIRED_IMAGES) {
      const row = doc.images[image];
      for (const platform of row.platforms) {
        const identity = row.platform_manifests[platform];
        console.log(
          [
            image,
            row.digest,
            platform,
            identity.manifest_digest,
            identity.config_digest,
          ].join("\t"),
        );
      }
    }
    return;
  }
  if (command === "check-source-inputs") {
    const drift = runtimeInputDrift(doc.build_source, arg || "HEAD");
    assertNoRuntimeInputDrift(drift);
    console.log(
      `✓ runtime image inputs are tree-identical: ${doc.build_source} → ${arg || "HEAD"}`,
    );
    return;
  }
  usage();
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(`candidate-image-map: ${error.message}`);
    process.exit(1);
  }
}
