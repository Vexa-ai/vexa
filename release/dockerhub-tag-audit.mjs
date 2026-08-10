import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const DOCKERHUB_API = "https://hub.docker.com";
export const RELEASE_REPOSITORIES = [
  "v012-admin-api",
  "v012-runtime",
  "v012-agent-worker",
  "v012-agent-api",
  "v012-meeting-api",
  "v012-gateway",
  "v012-mcp",
  "v012-terminal",
  "vexa-bot",
  "vexa-lite",
];
export const DELTA_REPOSITORIES = ["vexa-bot", "vexa-lite"];

export function previousAttemptTag(targetTag) {
  const match = targetTag.match(/^(.+\.packet)([0-9]+)$/);
  if (!match || Number(match[2]) <= 1) {
    throw new Error(
      "Bot+Lite delta tags must end in .packetN with N > 1 so the prior attempt is auditable",
    );
  }
  return `${match[1]}${Number(match[2]) - 1}`;
}

async function requestWithRetry(url, options, fetchImpl, wait, attempts = 3) {
  let response;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    response = await fetchImpl(url, options);
    if (response.status !== 429 && response.status < 500) return response;
    if (attempt < attempts) await wait(2_000);
  }
  return response;
}

export async function auditCandidateTags({
  targetTag,
  username,
  secret,
  outputPath,
  apiBase = DOCKERHUB_API,
  fetchImpl = fetch,
  wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  if (!username || !secret) throw new Error("Docker Hub credentials are required");
  writeFileSync(outputPath, "");

  const auth = await requestWithRetry(
    `${apiBase}/v2/auth/token`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ identifier: username, secret }),
    },
    fetchImpl,
    wait,
  );
  if (!auth.ok) throw new Error(`Docker Hub authentication returned HTTP ${auth.status}`);
  const accessToken = (await auth.json()).access_token;
  if (typeof accessToken !== "string" || accessToken.length === 0) {
    throw new Error("Docker Hub authentication returned no access_token");
  }
  console.log(`::add-mask::${accessToken}`);

  const previousTag = previousAttemptTag(targetTag);
  const rows = [];
  const auditAbsent = async (repository, tag) => {
    const ref = `vexaai/${repository}:${tag}`;
    const response = await requestWithRetry(
      `${apiBase}/v2/namespaces/vexaai/repositories/${repository}/tags/${tag}`,
      {
        method: "HEAD",
        headers: { authorization: `Bearer ${accessToken}` },
      },
      fetchImpl,
      wait,
    );
    if (response.status === 404) {
      rows.push(`ABSENT\t${ref}\tDocker-Hub-API-404`);
      writeFileSync(outputPath, `${rows.join("\n")}\n`);
      return;
    }
    if (response.status === 200) {
      rows.push(`EXISTS\t${ref}\tDocker-Hub-API-200`);
      writeFileSync(outputPath, `${rows.join("\n")}\n`);
      throw new Error(`${ref} already exists`);
    }
    rows.push(`INCONCLUSIVE\t${ref}\tDocker-Hub-API-${response.status}`);
    writeFileSync(outputPath, `${rows.join("\n")}\n`);
    throw new Error(`Docker Hub API readback for ${ref} returned HTTP ${response.status}`);
  };

  for (const repository of RELEASE_REPOSITORIES) {
    await auditAbsent(repository, previousTag);
  }
  for (const repository of DELTA_REPOSITORIES) {
    await auditAbsent(repository, targetTag);
  }
  return { previousTag, rows };
}

function parseArgs(argv) {
  const args = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    args.set(argv[index], argv[index + 1]);
  }
  const targetTag = args.get("--target");
  const outputPath = args.get("--output");
  if (!targetTag || !outputPath || args.size !== 2) {
    throw new Error(
      "usage: dockerhub-tag-audit.mjs --target <vX.Y.Z-*.packetN> --output <audit.tsv>",
    );
  }
  return { targetTag, outputPath };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const { targetTag, outputPath } = parseArgs(process.argv.slice(2));
    const result = await auditCandidateTags({
      targetTag,
      outputPath,
      username: process.env.DH_USER,
      secret: process.env.DH_TOKEN,
    });
    for (const row of result.rows) console.log(row);
    console.log(
      `✓ ${result.previousTag} and ${targetTag} replacement refs are conclusively absent`,
    );
  } catch (error) {
    console.error(`dockerhub-tag-audit: ${error.message}`);
    process.exit(1);
  }
}
