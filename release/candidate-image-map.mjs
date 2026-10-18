#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import {
  SERVICES,
  imageFor,
  legacyImageFor,
  legacyRepositoryFor,
  repositoryFor,
  serviceOf,
} from "./image-names.mjs";

// Image names come from ONE place: release/image-names.mjs. Schema 1-3 packets were frozen under
// the `vexaai/v012-<service>` names and keep validating against them; schema 4 (v0.13.3+) names the
// same images `vexaai/vexa-<service>`.
const L = legacyImageFor;

export const REQUIRED_IMAGES = [
  L("admin-api"),
  L("runtime"),
  L("agent-worker"),
  L("agent-api"),
  L("meeting-api"),
  L("gateway"),
  L("mcp"),
  L("terminal"),
  "vexaai/vexa-bot",
  "vexaai/vexa-lite",
];

// schema_version 2 (v0.12.27+): the flows domain ships as its own image — one image, three
// entrypoints (worker · mailbox · api) — because deploy/compose and the chart run it as three
// services. Frozen schema-1 packets keep validating against the ten; a v0.12.27+ map must name
// eleven. Production runs its own derivative (vexaai/vexa-flows-platform), so the OSS image is
// oss_only.
export const FLOWS_IMAGE = L("flows");
export const REQUIRED_IMAGES_V2 = [...REQUIRED_IMAGES, FLOWS_IMAGE];

// schema_version 3 (v0.13.2+): Connections ships the credential broker as its own image
// (core/agent/services/credential-broker, ADR-0040) — compose and the chart run it beside
// agent-api. Frozen schema-1 and schema-2 packets keep validating against ten and eleven; a
// v0.13.2+ map must name twelve. Hosted production does not deploy it yet, so it is oss_only.
export const CREDENTIAL_BROKER_IMAGE = L("credential-broker");
export const REQUIRED_IMAGES_V3 = [...REQUIRED_IMAGES_V2, CREDENTIAL_BROKER_IMAGE];

// schema_version 4 (v0.13.3+): the same twelve images under their `vexaai/vexa-<service>` names.
// v0.13.3 also pushes each under its v012- name with the same digest (image-names.mjs).
const RENAME = new Map(SERVICES.map((s) => [legacyImageFor(s), imageFor(s)]));
export const REQUIRED_IMAGES_V4 = REQUIRED_IMAGES_V3.map((i) => RENAME.get(i) || i);
export const CURRENT_SCHEMA_VERSION = 4;
export const CURRENT_REQUIRED_IMAGES = REQUIRED_IMAGES_V4;

export function requiredImagesFor(doc) {
  if (!doc) return CURRENT_REQUIRED_IMAGES;
  if (doc.schema_version === 4) return REQUIRED_IMAGES_V4;
  if (doc.schema_version === 3) return REQUIRED_IMAGES_V3;
  return doc.schema_version === 2 ? REQUIRED_IMAGES_V2 : REQUIRED_IMAGES;
}

const PROD_DEPLOYED_SERVICES = ["admin-api", "runtime", "meeting-api", "gateway"];
export const PROD_DEPLOYED_IMAGES = new Set([
  ...PROD_DEPLOYED_SERVICES.flatMap((s) => [legacyImageFor(s), imageFor(s)]),
  "vexaai/vexa-bot",
]);

// Every path that can enter each release image. Root-context builds include the
// root .dockerignore because changing it changes which bytes Docker receives.
// Narrow contexts name their whole context. Lite uses Dockerfile.lite.dockerignore
// instead of the root ignore file.
const RUNTIME_INPUTS_BY_SERVICE = {
  "admin-api": [
    "core/identity/services/admin-api",
  ],
  "runtime": [
    "core/runtime",
  ],
  "agent-worker": [
    ".dockerignore",
    "core/agent",
    "core/meetings/contracts/transcript.v1/transcript.schema.json",
  ],
  "agent-api": [
    ".dockerignore",
    "core/agent",
    "core/meetings/contracts/transcript.v1/transcript.schema.json",
  ],
  "meeting-api": [
    ".dockerignore",
    "core/meetings/services/meeting-api",
    "core/meetings/mcp.tools.v1.json",
    "core/meetings/contracts/invocation.v1/invocation.schema.json",
    "core/meetings/contracts/lifecycle.v1/lifecycle.schema.json",
    "core/meetings/contracts/webhook.v1/webhook.schema.json",
    "core/runtime/contracts/runtime.v1/runtime.schema.json",
    "core/runtime/contracts/schedule.v1/schedule.schema.json",
  ],
  "gateway": [
    ".dockerignore",
    "core/gateway/services/gateway",
    "core/meetings/routes.v1.json",
    "core/gateway/services/mcp/routes.v1.json",
    "core/identity/routes.v1.json",
    "core/agent/routes.v1.json",
  ],
  "mcp": [
    "core/gateway/services/mcp",
  ],
  "terminal": [
    "clients/terminal",
  ],
  "flows": [
    ".dockerignore",
    "core/flows",
    "behavior",
  ],
  "credential-broker": [
    "core/agent/services/credential-broker",
  ],
};

export const RUNTIME_INPUTS_BY_IMAGE = {
  ...Object.fromEntries(SERVICES.flatMap((s) => [
    [legacyImageFor(s), RUNTIME_INPUTS_BY_SERVICE[s]],
    [imageFor(s), RUNTIME_INPUTS_BY_SERVICE[s]],
  ])),
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

const BUILD_BY_SERVICE = {
  "admin-api": {
    context: "core/identity/services/admin-api",
    dockerfile: "core/identity/services/admin-api/Dockerfile",
  },
  "runtime": {
    context: "core/runtime",
    dockerfile: "core/runtime/Dockerfile",
  },
  "agent-worker": {
    context: ".",
    dockerfile: "core/agent/worker/Dockerfile",
  },
  "agent-api": {
    context: ".",
    dockerfile: "core/agent/services/agent-api/Dockerfile",
  },
  "meeting-api": {
    context: ".",
    dockerfile: "core/meetings/services/meeting-api/Dockerfile",
  },
  "gateway": {
    context: ".",
    dockerfile: "core/gateway/services/gateway/Dockerfile",
  },
  "mcp": {
    context: "core/gateway/services/mcp",
    dockerfile: "core/gateway/services/mcp/Dockerfile",
  },
  "terminal": {
    context: "clients/terminal",
    dockerfile: "clients/terminal/Dockerfile",
    // THE VARIANT THIS LINE SHIPS. NEXT_PUBLIC_TERMINAL_MODE is baked into the Next.js bundle at
    // build time (clients/terminal/Dockerfile:33,78; src/platform/mode.ts:25) — it cannot be switched
    // by a runtime env var, so the release matrix is the only place that decides which terminal
    // the channel publishes. Until this line existed the matrix passed NO build args at all, so
    // the published terminal:<tag> was the full workbench while the demo the pilot was shown
    // (app.dev.vexa.ai) was the minutes lane — the same variant/tag mismatch the Dockerfile's own
    // note at :85-95 records from 2026-09-02, one layer up. The published image records the
    // outcome in LABEL ai.vexa.terminal.mode, which release-validate asserts on both the amd64
    // and arm64 identity legs; the packet (candidate-images.json) pins digests and knows nothing
    // about variant, so that label assertion is the only thing standing between this line and a
    // full-terminal publish.
    build_args: "NEXT_PUBLIC_TERMINAL_MODE=minutes",
  },
  "flows": {
    context: ".",
    dockerfile: "core/flows/Dockerfile",
  },
  // Narrow context: the broker's signer is vendored into its own src/, so nothing outside its
  // directory enters the image.
  "credential-broker": {
    context: "core/agent/services/credential-broker",
    dockerfile: "core/agent/services/credential-broker/Dockerfile",
  },
};

// A frozen schema 1-3 packet keeps building under the name it was frozen with. A current-name row
// also carries `legacy_repository` while the dual push lasts: release-images.yml pushes the one
// build under both tags, so both names hold the same digest.
export const BUILD_MATRIX_BY_IMAGE = {
  ...Object.fromEntries(SERVICES.flatMap((s) => [
    [legacyImageFor(s), { name: s, repository: legacyRepositoryFor(s), ...BUILD_BY_SERVICE[s] }],
    [imageFor(s), {
      name: s, repository: repositoryFor(s), legacy_repository: legacyRepositoryFor(s),
      ...BUILD_BY_SERVICE[s],
    }],
  ])),
  "vexaai/vexa-lite": {
    name: "lite",
    repository: "vexa-lite",
    context: ".",
    dockerfile: "deploy/lite/Dockerfile.lite",
    free_disk: true,
  },
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
  if (![1, 2, 3, 4].includes(doc.schema_version)) fail("schema_version must be 1, 2, 3 or 4");
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

  const requiredImages = requiredImagesFor(doc);
  const actual = Object.keys(doc.images).sort();
  const required = [...requiredImages].sort();
  if (actual.join("\n") !== required.join("\n")) {
    fail(`image set mismatch\nactual=${actual.join(",")}\nrequired=${required.join(",")}`);
  }

  for (const image of requiredImages) {
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
  return requiredImagesFor(doc).flatMap((image) => {
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

export function candidateChangedImages(doc, head = "HEAD", cwd = process.cwd()) {
  return requiredImagesFor(doc).filter((image) => {
    const row = doc.images[image];
    const buildSource = row.build_source || doc.build_source;
    return runtimeInputDriftForPaths(
      buildSource,
      head,
      RUNTIME_INPUTS_BY_IMAGE[image],
      cwd,
    ).length > 0;
  });
}

export function candidateBuildPlanFromChangedImages(doc, changedImages) {
  const requiredImages = requiredImagesFor(doc);
  const changed = [...new Set(changedImages)];
  const unknown = changed.filter((image) => !requiredImages.includes(image));
  if (unknown.length > 0) fail(`build plan contains unknown image(s): ${unknown.join(", ")}`);

  const botLite = ["vexaai/vexa-bot", "vexaai/vexa-lite"];
  const exactBotLite =
    changed.length === botLite.length &&
    botLite.every((image) => changed.includes(image));
  const exactFull =
    changed.length === requiredImages.length &&
    requiredImages.every((image) => changed.includes(image));

  if (!exactBotLite && !exactFull) {
    fail(
      "unsupported partial candidate build; only the independently validated " +
      `Bot+Lite delta is allowed (changed: ${changed.join(", ") || "none"})`,
    );
  }

  const selected = exactFull ? requiredImages : botLite;
  return {
    mode: exactFull ? "full" : "bot-lite-delta",
    changed_images: selected,
    build_matrix: selected
      .filter((image) => image !== "vexaai/vexa-bot")
      .map((image) => ({
        ...BUILD_MATRIX_BY_IMAGE[image],
        use_registry_cache: exactFull,
      })),
    build_bot: selected.includes("vexaai/vexa-bot"),
    base_candidate_tag: doc?.candidate_tag || null,
    // The base candidate's service images under the names it was frozen with (schema 1-3:
    // vexaai/v012-<service>; schema 4: vexaai/vexa-<service>). A delta leg pulls these, not
    // today's names, and retags them to today's names for compose.
    base_service_images: doc ? requiredImagesFor(doc).filter((image) => serviceOf(image)) : [],
  };
}

export function candidateBuildPlan(doc, head = "HEAD", cwd = process.cwd()) {
  if (!doc) {
    return candidateBuildPlanFromChangedImages(
      null,
      CURRENT_REQUIRED_IMAGES,
    );
  }
  return candidateBuildPlanFromChangedImages(
    doc,
    candidateChangedImages(doc, head, cwd),
  );
}

function loadMap(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function usage() {
  console.error(
    "usage: candidate-image-map.mjs " +
    "<check|emit-tsv|emit-platform-tsv|check-source-inputs|emit-build-plan> " +
    "<map.json> [expected-version|head]",
  );
  process.exit(2);
}

function main(argv) {
  const [command, path, arg] = argv;
  if (!command || !path) usage();
  const doc = path === "-"
    ? null
    : validateCandidateMap(loadMap(path), command === "check" ? arg : undefined);

  if (command === "check") {
    if (!doc) usage();
    console.log(`✓ ${doc.release}: exact ${requiredImagesFor(doc).length}-image candidate map (schema ${doc.schema_version}) is well formed`);
    return;
  }
  if (command === "emit-tsv") {
    if (!doc) usage();
    for (const image of requiredImagesFor(doc)) {
      const row = doc.images[image];
      console.log(`${image}\t${row.digest}\t${row.candidate_tag || doc.candidate_tag}`);
    }
    return;
  }
  if (command === "emit-platform-tsv") {
    if (!doc) usage();
    for (const image of requiredImagesFor(doc)) {
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
    if (!doc) usage();
    const drift = candidateInputDrift(doc, arg || "HEAD");
    assertNoRuntimeInputDrift(drift);
    console.log(
      `✓ every image input is tree-identical to its witnessed build source → ${arg || "HEAD"}`,
    );
    return;
  }
  if (command === "emit-build-plan") {
    console.log(JSON.stringify(candidateBuildPlan(doc, arg || "HEAD")));
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
