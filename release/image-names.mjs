#!/usr/bin/env node
// The ONE place a release image is named. Every other surface — the candidate map, the release
// workflows, compose, the Helm chart, Lite, docs — either derives from this module or is checked
// against it by `release/image-names.test.mjs` (which runs in gates.yml with the other release
// tests).
//
// Name shape: `vexaai/vexa-<service>:<version>`. Until v0.13.3 the service images were published as
// `vexaai/v012-<service>`. v0.13.3 is the ONE transition release: every service image is pushed
// under both names from the same build, so both names resolve to the same digest. From
// LEGACY_DUAL_PUSH_ENDS_AFTER's next release on, only the `vexa-` names are published.

import { pathToFileURL } from "node:url";

export const NAMESPACE = "vexaai";

/** The service images the release builds in its matrix, in publication order. */
export const SERVICES = [
  "admin-api",
  "runtime",
  "agent-worker",
  "agent-api",
  "meeting-api",
  "gateway",
  "mcp",
  "terminal",
  "flows",
  "credential-broker",
];

/** Released images whose name never carried the `v012-` prefix; they are not renamed. */
export const UNPREFIXED_IMAGES = ["vexaai/vexa-bot", "vexaai/vexa-lite"];

/** Opt-in images that compose and the chart name but no release publishes: the operator builds them
 *  from source (the CRM: compose profile `crm`, Helm `crm.enabled`; transcript signals: compose
 *  profile `signals`, Helm `signals.enabled` — all off by default). */
export const SOURCE_BUILT_IMAGES = ["vexaai/vexa-crm", "vexaai/vexa-signals"];

export const LEGACY_REPOSITORY_PREFIX = "v012-";
/** The last release that also publishes the `v012-` names. Its successor publishes `vexa-` only. */
export const LEGACY_DUAL_PUSH_ENDS_AFTER = "v0.13.3";

export const repositoryFor = (service) => `vexa-${service}`;
export const imageFor = (service) => `${NAMESPACE}/${repositoryFor(service)}`;
export const legacyRepositoryFor = (service) => `${LEGACY_REPOSITORY_PREFIX}${service}`;
export const legacyImageFor = (service) => `${NAMESPACE}/${legacyRepositoryFor(service)}`;

export const SERVICE_IMAGES = SERVICES.map(imageFor);
export const LEGACY_SERVICE_IMAGES = SERVICES.map(legacyImageFor);

const assertService = (service) => {
  if (!SERVICES.includes(service)) throw new Error(`unknown release service: ${service}`);
  return service;
};

/** `vexaai/vexa-mcp` or `vexaai/v012-mcp` → `mcp`; anything else → null. */
export function serviceOf(image) {
  const m = /^vexaai\/(?:vexa-|v012-)(.+)$/.exec(image);
  return m && SERVICES.includes(m[1]) ? m[1] : null;
}

const parse = (version) => {
  const m = /^v(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.]+)?$/.exec(version);
  if (!m) throw new Error(`not a release version: ${version}`);
  return m.slice(1, 4).map(Number);
};

/** True when `version` (including its -rc/-candidate suffixes) still publishes the legacy names. */
export function dualPushes(version) {
  const [a, b, c] = parse(version);
  const [x, y, z] = parse(LEGACY_DUAL_PUSH_ENDS_AFTER);
  return a < x || (a === x && (b < y || (b === y && c <= z)));
}

/** The legacy repository (no namespace) that must carry the same digest as `image` at `version`, or "". */
export function legacyAliasFor(image, version) {
  const service = serviceOf(image);
  if (!service || !image.startsWith(`${NAMESPACE}/vexa-`) || !dualPushes(version)) return "";
  return legacyRepositoryFor(assertService(service));
}

function main([command, ...args]) {
  switch (command) {
    case "images":        // every current release image, one per line
      for (const i of [...SERVICE_IMAGES, ...UNPREFIXED_IMAGES]) console.log(i);
      return;
    case "service-images":
      for (const i of SERVICE_IMAGES) console.log(i);
      return;
    case "dual-pushes": { // dual-pushes <version> → true|false
      if (!args[0]) throw new Error("usage: image-names.mjs dual-pushes <version>");
      console.log(String(dualPushes(args[0])));
      return;
    }
    case "legacy-alias": { // legacy-alias <image> <version> → legacy repository or empty line
      const [image, version] = args;
      if (!image || !version) throw new Error("usage: image-names.mjs legacy-alias <image> <version>");
      console.log(legacyAliasFor(image, version));
      return;
    }
    default:
      throw new Error("usage: image-names.mjs <images|service-images|dual-pushes <version>|legacy-alias <image> <version>>");
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(`image-names: ${error.message}`); process.exit(2); }
}
