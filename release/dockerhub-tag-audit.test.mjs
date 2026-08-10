import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  DELTA_REPOSITORIES,
  RELEASE_REPOSITORIES,
  auditCandidateTags,
  previousAttemptTag,
} from "./dockerhub-tag-audit.mjs";

function tempAudit(t) {
  const dir = mkdtempSync(join(tmpdir(), "dockerhub-tag-audit-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return join(dir, "audit.tsv");
}

function response(status, body = "") {
  return new Response(body, {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("derives the immediately preceding packet attempt", () => {
  assert.equal(
    previousAttemptTag("v0.12.18-260724.packet3"),
    "v0.12.18-260724.packet2",
  );
  assert.throws(() => previousAttemptTag("v0.12.18-stage2"), /must end in \.packetN/);
});

test("audits all prior refs and both target refs as absent", async (t) => {
  const outputPath = tempAudit(t);
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("/v2/auth/token")) {
      return response(200, JSON.stringify({ access_token: "short-lived-token" }));
    }
    return response(404);
  };
  const result = await auditCandidateTags({
    targetTag: "v0.12.18-260724.packet3",
    username: "ci-user",
    secret: "ci-secret",
    outputPath,
    apiBase: "https://docker.invalid",
    fetchImpl,
    wait: async () => {},
  });

  assert.equal(
    result.rows.length,
    RELEASE_REPOSITORIES.length + DELTA_REPOSITORIES.length,
  );
  assert.ok(result.rows.every((row) => row.startsWith("ABSENT\t")));
  assert.equal(readFileSync(outputPath, "utf8").trim().split("\n").length, 12);
  assert.equal(requests[0].options.method, "POST");
  assert.ok(requests.slice(1).every(({ options }) => options.method === "HEAD"));
  assert.deepEqual(
    requests.slice(1, 11).map(({ url }) => url.split("/").at(-1)),
    Array(RELEASE_REPOSITORIES.length).fill("v0.12.18-260724.packet2"),
  );
  assert.deepEqual(
    requests.slice(11).map(({ url }) => url.split("/").at(-1)),
    Array(DELTA_REPOSITORIES.length).fill("v0.12.18-260724.packet3"),
  );
  assert.ok(
    requests
      .slice(1)
      .every(({ options }) => options.headers.authorization === "Bearer short-lived-token"),
  );
});

test("an existing target fails closed and is preserved in the audit", async (t) => {
  const outputPath = tempAudit(t);
  const fetchImpl = async (url) => {
    if (url.endsWith("/v2/auth/token")) {
      return response(200, JSON.stringify({ access_token: "short-lived-token" }));
    }
    if (url.includes("/vexa-bot/tags/v0.12.18-260724.packet3")) return response(200);
    return response(404);
  };

  await assert.rejects(
    auditCandidateTags({
      targetTag: "v0.12.18-260724.packet3",
      username: "ci-user",
      secret: "ci-secret",
      outputPath,
      apiBase: "https://docker.invalid",
      fetchImpl,
      wait: async () => {},
    }),
    /already exists/,
  );
  assert.match(readFileSync(outputPath, "utf8"), /EXISTS\tvexaai\/vexa-bot/);
});

test("persistent throttling remains inconclusive and stops before a build", async (t) => {
  const outputPath = tempAudit(t);
  let tagAttempts = 0;
  const fetchImpl = async (url) => {
    if (url.endsWith("/v2/auth/token")) {
      return response(200, JSON.stringify({ access_token: "short-lived-token" }));
    }
    tagAttempts += 1;
    return response(429);
  };

  await assert.rejects(
    auditCandidateTags({
      targetTag: "v0.12.18-260724.packet3",
      username: "ci-user",
      secret: "ci-secret",
      outputPath,
      apiBase: "https://docker.invalid",
      fetchImpl,
      wait: async () => {},
    }),
    /HTTP 429/,
  );
  assert.equal(tagAttempts, 3);
  assert.match(readFileSync(outputPath, "utf8"), /INCONCLUSIVE/);
});
