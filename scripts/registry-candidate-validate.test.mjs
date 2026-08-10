import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  aliasManifest,
  RegistryValidationError,
  validateCandidateMap,
  validateManifestIdentity,
  verifyCandidateMapHash,
} from "./registry-candidate-validate.mjs";

const digest = (character) => `sha256:${character.repeat(64)}`;
const sha256 = (bytes) => `sha256:${createHash("sha256").update(bytes).digest("hex")}`;

test("frozen map hash accepts exact bytes and rejects altered or truncated bytes", () => {
  const raw = Buffer.from('{"images":{"vexaai/vexa-lite":{}}}\n');
  const hash = "1887b0e9d312f64c21e46b0bf45d3cff9504fd9042a1a29463e14230d520add3";
  assert.equal(verifyCandidateMapHash(raw, hash), hash);
  for (const changed of [Buffer.from('{"images":{}}\n'), raw.subarray(0, raw.length - 2)]) {
    assert.throws(
      () => verifyCandidateMapHash(changed, hash),
      (error) => error instanceof RegistryValidationError && error.kind === "identity",
    );
  }
});

test("v0.12.18 frozen map pins exactly 10 top and 19 platform identities", async () => {
  const raw = await readFile(new URL("../releases/v0.12.18/candidate-images.json", import.meta.url));
  assert.equal(
    verifyCandidateMapHash(
      raw,
      "80fe23246f94557279b4da2792119489f7c60f8e452b83de2c1c33ef48ebd03f",
    ),
    "80fe23246f94557279b4da2792119489f7c60f8e452b83de2c1c33ef48ebd03f",
  );
  const map = JSON.parse(raw);
  assert.equal(Object.keys(map.images).length, 10);
  assert.equal(
    Object.values(map.images).reduce(
      (count, image) => count + Object.keys(image.platform_manifests).length,
      0,
    ),
    19,
  );
});

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
  const payload =
    typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  return new Response(payload, {
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

test("moving alias readback uses the frozen source tag without weakening map identity", async () => {
  const value = fixture();
  const fetchImpl = async (url) => {
    if (String(url).includes("/token")) return response(200, { token: "scoped-token" });
    const childDigest = value.expected.platform_manifests["linux/amd64"].manifest_digest;
    if (String(url).endsWith(childDigest)) {
      return response(200, value.children.get(childDigest), {
        "docker-content-digest": childDigest,
      });
    }
    return response(200, value.top, {
      "docker-content-digest": value.expected.digest,
    });
  };
  const counts = await validateCandidateMap({
    candidateMap: oneImageMap(),
    tag: "v012",
    expectedStableTag: "v0.12.18",
    username: "user",
    password: "token",
    fetchImpl,
  });
  assert.deepEqual(counts, {
    topDescriptors: 1,
    platformIdentities: 1,
    attestationIdentities: 1,
  });

  await assert.rejects(
    validateCandidateMap({
      candidateMap: oneImageMap(),
      tag: "v012",
      expectedStableTag: "v0.12.19",
      username: "user",
      password: "token",
      fetchImpl: async () => {
        throw new Error("must stop before network");
      },
    }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "identity" &&
      error.message.includes("does not match frozen source v0.12.19"),
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

test("single-manifest alias writes the exact source bytes and preserves its top digest", async () => {
  const sourceBytes = Buffer.from(
    JSON.stringify({
      schemaVersion: 2,
      mediaType: "application/vnd.oci.image.manifest.v1+json",
      config: { digest: digest("3") },
      layers: [],
    }),
  );
  const sourceDigest = sha256(sourceBytes);
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).startsWith("https://auth.example/token")) {
      return response(200, { token: "push-token" });
    }
    const reference = decodeURIComponent(String(url).split("/manifests/")[1]);
    if (options.method === "PUT") {
      assert.equal(reference, "v012");
      assert.equal(options.headers["content-type"], "application/vnd.oci.image.manifest.v1+json");
      assert.deepEqual(Buffer.from(options.body), sourceBytes);
      return response(201, "", { "docker-content-digest": sourceDigest });
    }
    if (reference === "latest") {
      return response(200, sourceBytes, {
        "content-type": "application/vnd.oci.image.manifest.v1+json",
        "docker-content-digest": digest("9"),
      });
    }
    return response(200, sourceBytes, {
      "content-type": "application/vnd.oci.image.manifest.v1+json",
      "docker-content-digest": sourceDigest,
    });
  };

  const result = await aliasManifest({
    repository: "vexaai/vexa-bot",
    sourceReference: "v0.12.18",
    targetReference: "v012",
    expectedDigest: sourceDigest,
    unchangedReference: "latest",
    username: "user",
    password: "token",
    fetchImpl,
    authBase: "https://auth.example",
    registryBase: "https://registry.example",
    service: "registry.example",
  });

  assert.deepEqual(result, {
    sourceDigest,
    targetDigest: sourceDigest,
    unchangedDigest: digest("9"),
  });
  const tokenUrl = new URL(calls[0].url);
  assert.equal(tokenUrl.searchParams.get("scope"), "repository:vexaai/vexa-bot:pull,push");
});

test("red control: an imagetools-style index wrapper changes a single-manifest top digest", () => {
  const manifestBytes = Buffer.from(
    JSON.stringify({
      schemaVersion: 2,
      mediaType: "application/vnd.oci.image.manifest.v1+json",
      config: { digest: digest("3") },
      layers: [],
    }),
  );
  const manifestDigest = sha256(manifestBytes);
  const wrappedBytes = Buffer.from(
    JSON.stringify({
      schemaVersion: 2,
      mediaType: "application/vnd.oci.image.index.v1+json",
      manifests: [
        {
          mediaType: "application/vnd.oci.image.manifest.v1+json",
          digest: manifestDigest,
          size: manifestBytes.length,
        },
      ],
    }),
  );
  const wrappedDigest = sha256(wrappedBytes);

  assert.notEqual(wrappedDigest, manifestDigest);
  assert.throws(
    () =>
      validateManifestIdentity({
        repository: "vexaai/vexa-bot",
        expected: {
          digest: manifestDigest,
          platform_manifests: {
            "linux/amd64": {
              manifest_digest: manifestDigest,
              config_digest: digest("3"),
            },
          },
          attestations: false,
        },
        topDigest: wrappedDigest,
        manifest: JSON.parse(wrappedBytes),
        children: new Map(),
      }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "identity" &&
      error.message.includes("top descriptor mismatch"),
  );
});

test("alias readback digest rewrite is an identity failure", async () => {
  const bytes = Buffer.from('{"schemaVersion":2}');
  let reads = 0;
  await assert.rejects(
    aliasManifest({
      repository: "vexaai/vexa-bot",
      sourceReference: "v0.12.18",
      targetReference: "v012",
      expectedDigest: digest("1"),
      username: "user",
      password: "token",
      fetchImpl: async (url, options = {}) => {
        if (String(url).includes("/token")) return response(200, { token: "push-token" });
        if (options.method === "PUT") {
          return response(201, "", { "docker-content-digest": digest("1") });
        }
        reads += 1;
        return response(200, bytes, {
          "content-type": "application/vnd.oci.image.manifest.v1+json",
          "docker-content-digest": reads === 1 ? digest("1") : digest("4"),
        });
      },
      authBase: "https://auth.example",
      registryBase: "https://registry.example",
      service: "registry.example",
    }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "identity" &&
      error.message.includes("alias readback"),
  );
});

test("aliasing cannot move the unchanged latest negative control", async () => {
  const bytes = Buffer.from('{"schemaVersion":2}');
  let latestReads = 0;
  await assert.rejects(
    aliasManifest({
      repository: "vexaai/vexa-bot",
      sourceReference: "v0.12.18",
      targetReference: "v012",
      expectedDigest: digest("1"),
      unchangedReference: "latest",
      username: "user",
      password: "token",
      fetchImpl: async (url, options = {}) => {
        if (String(url).includes("/token")) return response(200, { token: "push-token" });
        const reference = decodeURIComponent(String(url).split("/manifests/")[1]);
        if (options.method === "PUT") {
          return response(201, "", { "docker-content-digest": digest("1") });
        }
        if (reference === "latest") {
          latestReads += 1;
          return response(200, bytes, {
            "content-type": "application/vnd.oci.image.manifest.v1+json",
            "docker-content-digest": latestReads === 1 ? digest("8") : digest("7"),
          });
        }
        return response(200, bytes, {
          "content-type": "application/vnd.oci.image.manifest.v1+json",
          "docker-content-digest": digest("1"),
        });
      },
      authBase: "https://auth.example",
      registryBase: "https://registry.example",
      service: "registry.example",
    }),
    (error) =>
      error instanceof RegistryValidationError &&
      error.kind === "identity" &&
      error.message.includes("negative-control descriptor moved"),
  );
});
