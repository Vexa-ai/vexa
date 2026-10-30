#!/usr/bin/env node
// Backfill the contributor registry from merged pull requests, read-only against GitHub.
//
// A merged PR registers its author as independent when it passes the same test the gate applies to
// an earlier declaration: exactly the independent box selected, no unresolved verifier review, and
// every edit to the body made by the author (a box a maintainer ticked does not count). Maintainers
// (OWNER/MEMBER/COLLABORATOR, or the `maintainers` list) need no entry. Existing entries are kept.
//
//   GITHUB_TOKEN=... node scripts/contribution-rights-backfill.mjs [--write]
//
// Without --write it prints the entries it would add. The script never edits a PR.

import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import {
  bodyEditedOnlyByAuthor, commentsForPullRequest, maintainerStanding, mergedDeclaration, registeredStanding, requestJson,
} from "./contribution-rights-gate.mjs";

const CONFIG = ".github/contribution-rights.json";

// The earliest qualifying merged PR per author, as registry entries keyed by login.
export async function backfillEntries(repo, config, apiBase, token) {
  const entries = {};
  const prs = [];
  for (let page = 1; page <= 50; page += 1) {
    const batch = await requestJson(`${apiBase}/repos/${repo}/pulls?state=closed&sort=created&direction=asc&per_page=100&page=${page}`, token);
    prs.push(...batch.filter((pr) => pr.merged_at && pr.number > config.effectiveAfterPullRequest));
    if (batch.length < 100) break;
  }
  for (const pr of prs) {
    const login = pr.user?.login;
    if (!login || pr.user?.type === "Bot" || entries[login]) continue;
    if (maintainerStanding(login, pr.author_association, config) || registeredStanding(login, config)) continue;
    if (!mergedDeclaration(pr, [], config)) continue;
    if (!mergedDeclaration(pr, await commentsForPullRequest(repo, pr.number, apiBase, token), config)) continue;
    if (!await bodyEditedOnlyByAuthor(repo, pr.number, login, apiBase, token)) continue;
    entries[login] = { path: "independent", declared: pr.merged_at.slice(0, 10), source: `PR #${pr.number}` };
  }
  return entries;
}

async function main() {
  const repo = process.env.GITHUB_REPOSITORY || "Vexa-ai/vexa";
  const config = JSON.parse(readFileSync(CONFIG, "utf8"));
  const entries = await backfillEntries(repo, config, "https://api.github.com", process.env.GITHUB_TOKEN);
  console.log(JSON.stringify(entries, null, 2));
  if (process.argv.includes("--write")) {
    config.contributors = { ...config.contributors, ...entries };
    writeFileSync(CONFIG, `${JSON.stringify(config, null, 2)}\n`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
