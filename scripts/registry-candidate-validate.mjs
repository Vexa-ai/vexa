#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const ACCEPT = [
  "application/vnd.oci.image.index.v1+json",
  "application/vnd.docker.distribution.manifest.list.v2+json",
  "application/vnd.oci.image.manifest.v1+json",
  "application/vnd.docker.distribution.manifest.v2+json",
].join(", ");

export class RegistryValidationError extends Error {
  constructor(kind, message, details = {}) {
    super(message);
    this.name = "RegistryValidationError";
    this.kind = kind;
    this.details = details;
  }
}

function classifyStatus(status) {
  if (status === 401 || status === 403) return "auth";
  if (status === 429) return "quota";
  if (status >= 500) return "network";
  return "registry";
}

async function request(fetchImpl, url, options, context) {
  let response;
  try {
    response = await fetchImpl(url, options);
  } catch (error) {
    throw new RegistryValidationError(
      "network",
      `${context}: network request failed: ${error.message}`,
      { cause: error.message },
    );
  }

  if (!response.ok) {
    const body = (await response.text()).slice(0, 500);
    const kind = classifyStatus(response.status);
    throw new RegistryValidationError(
      kind,
      `${context}: registry HTTP ${response.status}${body ? `: ${body}` : ""}`,
      { status: response.status, body },
    );
  }
  return response;
}

async function jsonResponse(response, context) {
  try {
    return await response.json();
  } catch (error) {
    throw new RegistryValidationError(
      "network",
      `${context}: registry returned an invalid JSON response: ${error.message}`,
      { cause: error.message },
    );
  }
}

function identity(condition, message, details = {}) {
  if (!condition) {
    throw new RegistryValidationError("identity", message, details);
  }
}

export function verifyCandidateMapHash(raw, expectedHash) {
  const actualHash = createHash("sha256").update(raw).digest("hex");
  identity(
    actualHash === expectedHash,
    `candidate map hash mismatch (expected sha256:${expectedHash}, actual sha256:${actualHash})`,
    { expected: `sha256:${expectedHash}`, actual: `sha256:${actualHash}`, level: "candidate-map" },
  );
  return actualHash;
}

function platformKey(platform) {
  return `${platform?.os ?? ""}/${platform?.architecture ?? ""}`;
}

function isAttestation(descriptor) {
  return descriptor.annotations?.["vnd.docker.reference.type"] === "attestation-manifest";
}

export function validateManifestIdentity({ repository, expected, topDigest, manifest, children }) {
  identity(
    topDigest === expected.digest,
    `${repository}: top descriptor mismatch (expected ${expected.digest}, actual ${topDigest || "<missing>"})`,
    { repository, expected: expected.digest, actual: topDigest || null, level: "top" },
  );

  const expectedPlatforms = expected.platform_manifests;
  const descriptors = Array.isArray(manifest.manifests) ? manifest.manifests : null;

  if (!descriptors) {
    const keys = Object.keys(expectedPlatforms);
    identity(
      keys.length === 1,
      `${repository}: single manifest cannot satisfy ${keys.length} expected platforms`,
      { repository, expected_platforms: keys },
    );
    const key = keys[0];
    const expectedPlatform = expectedPlatforms[key];
    identity(
      expectedPlatform.manifest_digest === topDigest,
      `${repository} ${key}: manifest mismatch (expected ${expectedPlatform.manifest_digest}, actual ${topDigest})`,
      { repository, platform: key, expected: expectedPlatform.manifest_digest, actual: topDigest },
    );
    identity(
      manifest.config?.digest === expectedPlatform.config_digest,
      `${repository} ${key}: config mismatch (expected ${expectedPlatform.config_digest}, actual ${manifest.config?.digest || "<missing>"})`,
      {
        repository,
        platform: key,
        expected: expectedPlatform.config_digest,
        actual: manifest.config?.digest || null,
      },
    );
    identity(!expected.attestations, `${repository}: candidate map forbids attestations on a single manifest`);
    return { platforms: 1, attestations: 0 };
  }

  const platformDescriptors = new Map(
    descriptors.filter((item) => !isAttestation(item)).map((item) => [platformKey(item.platform), item]),
  );
  const attestations = descriptors.filter(isAttestation);

  identity(
    platformDescriptors.size === Object.keys(expectedPlatforms).length,
    `${repository}: platform descriptor count mismatch (expected ${Object.keys(expectedPlatforms).length}, actual ${platformDescriptors.size})`,
    { repository, expected: Object.keys(expectedPlatforms).length, actual: platformDescriptors.size },
  );

  for (const [key, expectedPlatform] of Object.entries(expectedPlatforms)) {
    const descriptor = platformDescriptors.get(key);
    identity(descriptor, `${repository}: expected platform ${key} is absent`, { repository, platform: key });
    identity(
      descriptor.digest === expectedPlatform.manifest_digest,
      `${repository} ${key}: manifest mismatch (expected ${expectedPlatform.manifest_digest}, actual ${descriptor.digest})`,
      {
        repository,
        platform: key,
        expected: expectedPlatform.manifest_digest,
        actual: descriptor.digest,
      },
    );
    const child = children.get(descriptor.digest);
    identity(child, `${repository} ${key}: child manifest ${descriptor.digest} was not resolved`);
    identity(
      child.config?.digest === expectedPlatform.config_digest,
      `${repository} ${key}: config mismatch (expected ${expectedPlatform.config_digest}, actual ${child.config?.digest || "<missing>"})`,
      {
        repository,
        platform: key,
        expected: expectedPlatform.config_digest,
        actual: child.config?.digest || null,
      },
    );
  }

  if (expected.attestations) {
    const referenced = new Set(
      attestations.map((item) => item.annotations?.["vnd.docker.reference.digest"]).filter(Boolean),
    );
    for (const expectedPlatform of Object.values(expectedPlatforms)) {
      identity(
        referenced.has(expectedPlatform.manifest_digest),
        `${repository}: attestation for ${expectedPlatform.manifest_digest} is absent`,
        { repository, manifest_digest: expectedPlatform.manifest_digest },
      );
    }
    identity(
      attestations.length === Object.keys(expectedPlatforms).length,
      `${repository}: attestation descriptor count mismatch (expected ${Object.keys(expectedPlatforms).length}, actual ${attestations.length})`,
      { repository, expected: Object.keys(expectedPlatforms).length, actual: attestations.length },
    );
  } else {
    identity(
      attestations.length === 0,
      `${repository}: candidate map forbids attestations, found ${attestations.length}`,
      { repository, actual: attestations.length },
    );
  }

  return { platforms: platformDescriptors.size, attestations: attestations.length };
}

async function bearerToken({ fetchImpl, authBase, service, repository, username, password }) {
  const url = new URL("/token", authBase);
  url.searchParams.set("service", service);
  url.searchParams.set("scope", `repository:${repository}:pull`);
  const response = await request(
    fetchImpl,
    url,
    { headers: { authorization: `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}` } },
    `${repository}: token exchange`,
  );
  const body = await jsonResponse(response, `${repository}: token exchange`);
  if (!body.token && !body.access_token) {
    throw new RegistryValidationError("auth", `${repository}: token exchange returned no bearer token`);
  }
  return body.token ?? body.access_token;
}

async function registryManifest({ fetchImpl, registryBase, repository, reference, token }) {
  const url = new URL(`/v2/${repository}/manifests/${reference}`, registryBase);
  const response = await request(
    fetchImpl,
    url,
    { headers: { authorization: `Bearer ${token}`, accept: ACCEPT } },
    `${repository}@${reference}: manifest read`,
  );
  return {
    digest: response.headers.get("docker-content-digest"),
    body: await jsonResponse(response, `${repository}@${reference}: manifest read`),
  };
}

export async function validateCandidateMap({
  candidateMap,
  tag,
  username,
  password,
  fetchImpl = fetch,
  authBase = "https://auth.docker.io",
  registryBase = "https://registry-1.docker.io",
  service = "registry.docker.io",
  expectedTopDescriptors,
  expectedPlatformIdentities,
}) {
  if (!username || !password) {
    throw new RegistryValidationError("auth", "DOCKERHUB_USERNAME and DOCKERHUB_TOKEN are required");
  }
  identity(candidateMap.stable_tag === tag, `candidate map stable_tag ${candidateMap.stable_tag} does not match ${tag}`);

  let topDescriptors = 0;
  let platformIdentities = 0;
  let attestationIdentities = 0;

  for (const [fullRepository, expected] of Object.entries(candidateMap.images)) {
    const repository = fullRepository.replace(/^docker\.io\//, "");
    const token = await bearerToken({
      fetchImpl,
      authBase,
      service,
      repository,
      username,
      password,
    });
    const top = await registryManifest({
      fetchImpl,
      registryBase,
      repository,
      reference: tag,
      token,
    });
    const children = new Map();
    if (Array.isArray(top.body.manifests)) {
      for (const descriptor of top.body.manifests.filter((item) => !isAttestation(item))) {
        const child = await registryManifest({
          fetchImpl,
          registryBase,
          repository,
          reference: descriptor.digest,
          token,
        });
        identity(
          child.digest === descriptor.digest,
          `${repository}: child response digest mismatch (expected ${descriptor.digest}, actual ${child.digest || "<missing>"})`,
        );
        children.set(descriptor.digest, child.body);
      }
    }
    const counts = validateManifestIdentity({
      repository,
      expected,
      topDigest: top.digest,
      manifest: top.body,
      children,
    });
    topDescriptors += 1;
    platformIdentities += counts.platforms;
    attestationIdentities += counts.attestations;
    console.log(`✓ ${repository}:${tag} ${expected.digest}`);
  }

  if (expectedTopDescriptors !== undefined) {
    identity(
      topDescriptors === expectedTopDescriptors,
      `candidate population mismatch (expected ${expectedTopDescriptors} top descriptors, actual ${topDescriptors})`,
      { expected: expectedTopDescriptors, actual: topDescriptors, level: "population" },
    );
  }
  if (expectedPlatformIdentities !== undefined) {
    identity(
      platformIdentities === expectedPlatformIdentities,
      `candidate population mismatch (expected ${expectedPlatformIdentities} platform identities, actual ${platformIdentities})`,
      { expected: expectedPlatformIdentities, actual: platformIdentities, level: "population" },
    );
  }

  return { topDescriptors, platformIdentities, attestationIdentities };
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--candidate-map") result.candidateMap = argv[++index];
    else if (key === "--tag") result.tag = argv[++index];
    else if (key === "--expected-map-sha256") result.expectedMapHash = argv[++index];
    else if (key === "--expected-top-descriptors") result.expectedTopDescriptors = Number(argv[++index]);
    else if (key === "--expected-platform-identities") result.expectedPlatformIdentities = Number(argv[++index]);
    else throw new Error(`unknown argument: ${key}`);
  }
  if (
    !result.candidateMap ||
    !result.tag ||
    !/^[0-9a-f]{64}$/.test(result.expectedMapHash ?? "") ||
    !Number.isInteger(result.expectedTopDescriptors) ||
    !Number.isInteger(result.expectedPlatformIdentities)
  ) {
    throw new Error(
      "usage: registry-candidate-validate.mjs --candidate-map <file> --tag <vX.Y.Z> " +
        "--expected-map-sha256 <64-hex> --expected-top-descriptors <n> --expected-platform-identities <n>",
    );
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const raw = await readFile(args.candidateMap);
  const mapHash = verifyCandidateMapHash(raw, args.expectedMapHash);
  const candidateMap = JSON.parse(raw);
  const counts = await validateCandidateMap({
    candidateMap,
    tag: args.tag,
    username: process.env.DOCKERHUB_USERNAME,
    password: process.env.DOCKERHUB_TOKEN,
    expectedTopDescriptors: args.expectedTopDescriptors,
    expectedPlatformIdentities: args.expectedPlatformIdentities,
  });
  console.log(
    `✓ authenticated candidate-map validation: ${counts.topDescriptors} top descriptors, ` +
      `${counts.platformIdentities} platform manifest/config identities, ` +
      `${counts.attestationIdentities} linked attestations; map sha256:${mapHash}`,
  );
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    if (error instanceof RegistryValidationError) {
      console.error(`registry-candidate-validate [${error.kind}]: ${error.message}`);
      process.exitCode = error.kind === "identity" ? 4 : error.kind === "auth" ? 5 : error.kind === "quota" ? 6 : 7;
    } else {
      console.error(`registry-candidate-validate [internal]: ${error.stack || error.message}`);
      process.exitCode = 2;
    }
  });
}
