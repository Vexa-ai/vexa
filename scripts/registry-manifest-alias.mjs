#!/usr/bin/env node

import { readFile } from "node:fs/promises";

import {
  aliasManifest,
  RegistryValidationError,
  verifyCandidateMapHash,
} from "./registry-candidate-validate.mjs";

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--candidate-map") result.candidateMap = argv[++index];
    else if (key === "--expected-map-sha256") result.expectedMapHash = argv[++index];
    else if (key === "--repository") result.repository = argv[++index];
    else if (key === "--source-tag") result.sourceTag = argv[++index];
    else if (key === "--target-tag") result.targetTag = argv[++index];
    else if (key === "--assert-unchanged-tag") result.unchangedTag = argv[++index];
    else throw new Error(`unknown argument: ${key}`);
  }
  if (
    !result.candidateMap ||
    !/^[0-9a-f]{64}$/.test(result.expectedMapHash ?? "") ||
    !result.repository ||
    !result.sourceTag ||
    !result.targetTag
  ) {
    throw new Error(
      "usage: registry-manifest-alias.mjs --candidate-map <file> " +
        "--expected-map-sha256 <64-hex> --repository <owner/name> " +
        "--source-tag <tag> --target-tag <tag> [--assert-unchanged-tag <tag>]",
    );
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const raw = await readFile(args.candidateMap);
  verifyCandidateMapHash(raw, args.expectedMapHash);
  const candidateMap = JSON.parse(raw);
  const expected = candidateMap.images[args.repository];
  if (!expected) {
    throw new RegistryValidationError(
      "identity",
      `${args.repository}: repository is not present in the frozen candidate map`,
    );
  }
  if (candidateMap.stable_tag !== args.sourceTag) {
    throw new RegistryValidationError(
      "identity",
      `candidate map stable_tag ${candidateMap.stable_tag} does not match source ${args.sourceTag}`,
    );
  }

  const receipt = await aliasManifest({
    repository: args.repository,
    sourceReference: args.sourceTag,
    targetReference: args.targetTag,
    expectedDigest: expected.digest,
    unchangedReference: args.unchangedTag,
    username: process.env.DOCKERHUB_USERNAME,
    password: process.env.DOCKERHUB_TOKEN,
  });
  console.log(
    `✓ exact manifest alias ${args.repository}:${args.targetTag} = ${receipt.targetDigest}` +
      (args.unchangedTag
        ? `; ${args.unchangedTag} unchanged at ${receipt.unchangedDigest}`
        : ""),
  );
}

main().catch((error) => {
  if (error instanceof RegistryValidationError) {
    console.error(`registry-manifest-alias [${error.kind}]: ${error.message}`);
    process.exitCode =
      error.kind === "identity" ? 4 : error.kind === "auth" ? 5 : error.kind === "quota" ? 6 : 7;
  } else {
    console.error(`registry-manifest-alias [internal]: ${error.stack || error.message}`);
    process.exitCode = 2;
  }
});
