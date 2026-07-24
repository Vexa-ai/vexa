import assert from "node:assert/strict";
import test from "node:test";

import {
  RegistryValidationError,
  validateCandidateMap,
  validateManifestIdentity,
} from "./registry-candidate-validate.mjs";

const digest = (character) => `sha256:${character.repeat(64)}`;

function fixture() {
  const platform = digest("2");
  const config = digest("3");
  const attestation = digest("4");
  return {
    expected: {
      digest: digest("1"),
      platform_manifests: {
        "linux/amd64": { manifest_digest: platform, config_digest: config },
      },
      attestations: true,
    },
    top: {
      schemaVersion: 2,
      manifests: [
        { digest: platform, platform: { os: "linux", architecture: "amd64" } },
        {
          digest: attestation,
          platform: { os: "unknown", architecture: "unknown" },
          annotations: {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": platform,
          },
        },
      ],
    },
    children: new Map([[platform, { schemaVersion: 2, config: { digest: config } }]]),
  };
}

test("exact top, platform/config, and linked attestation identities pass", () => {
  const value = fixture();
  assert.deepEqual(
    validateManifestIdentity({
      repository: "vexaai/vexa-lite",
      expected: value.expected,
      topDigest: digest("1"),
      manifest: value.top,
      children: value.children,
    }),
    { platforms: 1, attestations: 1 },
  );
});

test("altered Lite top identity is classified as identity mismatch", () => {
  const value = fixture();
  assert.throws(
    () =>
      validateManifestIdentity({
        repository: "vexaai/vexa-lite",
        expected: value.expected,
        topDigest: digest("9"),
        manifest: value.top,
        children: value.children,
      }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "identity" &&
      error.message.includes(`expected ${digest("1")}, actual ${digest("9")}`),
  );
});

function response(status, body, headers = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function oneImageMap() {
  const value = fixture();
  return {
    stable_tag: "v0.12.18",
    images: { "vexaai/vexa-lite": value.expected },
  };
}

test("injected HTTP 429 is quota, never platform/identity", async () => {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(String(url));
    if (calls.length === 1) return response(200, { token: "scoped-token" });
    return response(429, { errors: [{ code: "TOOMANYREQUESTS" }] });
  };
  await assert.rejects(
    validateCandidateMap({
      candidateMap: oneImageMap(),
      tag: "v0.12.18",
      username: "user",
      password: "token",
      fetchImpl,
    }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "quota" &&
      !error.message.includes("platform"),
  );
});

test("injected HTTP 401 is auth", async () => {
  await assert.rejects(
    validateCandidateMap({
      candidateMap: oneImageMap(),
      tag: "v0.12.18",
      username: "user",
      password: "bad",
      fetchImpl: async () => response(401, { message: "unauthorized" }),
    }),
    (error) => error instanceof RegistryValidationError && error.kind === "auth",
  );
});

test("injected network failure is network", async () => {
  await assert.rejects(
    validateCandidateMap({
      candidateMap: oneImageMap(),
      tag: "v0.12.18",
      username: "user",
      password: "token",
      fetchImpl: async () => {
        throw new Error("socket reset");
      },
    }),
    (error) => error instanceof RegistryValidationError && error.kind === "network",
  );
});

test("invalid registry JSON is a transport response failure, not identity", async () => {
  let calls = 0;
  await assert.rejects(
    validateCandidateMap({
      candidateMap: oneImageMap(),
      tag: "v0.12.18",
      username: "user",
      password: "token",
      fetchImpl: async () => {
        calls += 1;
        if (calls === 1) return response(200, { token: "scoped-token" });
        return response(200, "not-json", { "content-type": "text/plain" });
      },
    }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "network" &&
      !error.message.includes("identity"),
  );
});
