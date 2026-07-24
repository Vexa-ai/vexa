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

// Every path that can enter each release image. Root-context builds include the
// root .dockerignore because changing it changes which bytes Docker receives.
// Narrow contexts name their whole context. Lite uses Dockerfile.lite.dockerignore
// instead of the root ignore file.
export const RUNTIME_INPUTS_BY_IMAGE = {
  "vexaai/v012-admin-api": [
    "core/identity/services/admin-api",
  ],
  "vexaai/v012-runtime": [
    "core/runtime",
  ],
  "vexaai/v012-agent-worker": [
    ".dockerignore",
    "core/agent",
    "core/meetings/contracts/transcript.v1/transcript.schema.json",
  ],
  "vexaai/v012-agent-api": [
    ".dockerignore",
    "core/agent",
    "core/meetings/contracts/transcript.v1/transcript.schema.json",
  ],
  "vexaai/v012-meeting-api": [
    ".dockerignore",
    "core/meetings/services/meeting-api",
    "core/meetings/contracts/invocation.v1/invocation.schema.json",
    "core/meetings/contracts/lifecycle.v1/lifecycle.schema.json",
    "core/meetings/contracts/webhook.v1/webhook.schema.json",
    "core/runtime/contracts/runtime.v1/runtime.schema.json",
    "core/runtime/contracts/schedule.v1/schedule.schema.json",
  ],
  "vexaai/v012-gateway": [
    "core/gateway/services/gateway",
  ],
  "vexaai/v012-mcp": [
    "core/meetings/services/mcp",
  ],
  "vexaai/v012-terminal": [
    "clients/terminal",
  ],
  "vexaai/vexa-bot": [
    ".dockerignore",
    "core",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
    "turbo.json",
    "licenses",
  ],
  "vexaai/vexa-lite": [
    "deploy/lite",
    "core",
    "scripts",
    "clients/terminal",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "tsconfig.base.json",
    "turbo.json",
    "licenses",
  ],
};

export const RUNTIME_INPUT_PATHS = [
  ...new Set(Object.values(RUNTIME_INPUTS_BY_IMAGE).flat()),
].sort();

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
    const overrideFields = [
      "candidate_tag",
      "build_source",
      "validation_source",
      "validation_run",
    ];
    const overrideCount = overrideFields.filter((field) => row[field] !== undefined).length;
    if (overrideCount !== 0 && overrideCount !== overrideFields.length) {
      fail(`${image}: candidate override must define ${overrideFields.join(",")}`);
    }
    if (overrideCount === overrideFields.length) {
      if (
        typeof row.candidate_tag !== "string" ||
        !row.candidate_tag.startsWith(`${doc.release}-`)
      ) {
        fail(`${image}: invalid candidate_tag override`);
      }
      if (!SHA.test(row.build_source)) fail(`${image}: invalid build_source override`);
      if (!SHA.test(row.validation_source)) fail(`${image}: invalid validation_source override`);
      if (
        !/^https:\/\/github\.com\/Vexa-ai\/vexa\/actions\/runs\/\d+$/.test(
          row.validation_run || "",
        )
      ) {
        fail(`${image}: invalid validation_run override`);
      }
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
  return runtimeInputDriftForPaths(buildSource, head, RUNTIME_INPUT_PATHS, cwd);
}

export function runtimeInputDriftForPaths(
  buildSource,
  head,
  inputPaths,
  cwd = process.cwd(),
) {
  const output = execFileSync(
    "git",
    ["diff", "--name-only", `${buildSource}..${head}`, "--", ...inputPaths],
    { cwd, encoding: "utf8" },
  );
  return output.split("\n").map((line) => line.trim()).filter(Boolean);
}

export function candidateInputDrift(doc, head = "HEAD", cwd = process.cwd()) {
  return REQUIRED_IMAGES.flatMap((image) => {
    const row = doc.images[image];
    const buildSource = row.build_source || doc.build_source;
    return runtimeInputDriftForPaths(
      buildSource,
      head,
      RUNTIME_INPUTS_BY_IMAGE[image],
      cwd,
    ).map((path) => `${image}: ${path}`);
  });
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
      const row = doc.images[image];
      console.log(`${image}\t${row.digest}\t${row.candidate_tag || doc.candidate_tag}`);
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
    const drift = candidateInputDrift(doc, arg || "HEAD");
    assertNoRuntimeInputDrift(drift);
    console.log(
      `✓ every image input is tree-identical to its witnessed build source → ${arg || "HEAD"}`,
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
