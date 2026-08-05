#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const RIGHTS = ["independent", "corporate", "uncertain"];
const DECISION_MARKER = "<!-- vexa-contribution-rights-decision:v1 -->";

function selectedRights(body = "") {
  return RIGHTS.filter((right) => {
    const marker = `<!-- rights:${right} -->`;
    const line = body.split("\n").find((candidate) => candidate.includes(marker)) || "";
    return /^\s*-\s*\[[xX]\]/.test(line);
  });
}

function field(body, name) {
  const pattern = `^\\s*(?:[-*]\\s*)?${name}:\\s*\\x60?([^\\n\\x60]+)\\x60?\\s*$`;
  const match = body.match(new RegExp(pattern, "im"));
  return match?.[1]?.trim();
}

function parseDecision(comment, config, pr) {
  const body = comment?.body || "";
  const login = comment?.user?.login?.toLowerCase();
  if (!body.includes(DECISION_MARKER) || !config.verifiers.map((v) => v.toLowerCase()).includes(login)) {
    return null;
  }

  const decision = field(body, "Decision")?.toLowerCase();
  const prNumber = field(body, "PR")?.replace(/^#/, "");
  const head = field(body, "Head")?.toLowerCase();
  const receipt = field(body, "Receipt");
  if (!["review", "cleared", "verified"].includes(decision) || Number(prNumber) !== pr.number) return null;
  if (!/^[0-9a-f]{40}$/.test(head || "")) return null;
  if (decision === "verified" && !/^VCR-[0-9]{4}-[0-9]{4,}$/.test(receipt || "")) return null;

  return {
    decision,
    head,
    receipt,
    login: comment.user.login,
    order: Number(comment.id || 0) || Date.parse(comment.created_at || comment.updated_at || 0),
  };
}

export function evaluatePullRequest(pr, comments, config) {
  if (!Number.isInteger(config.effectiveAfterPullRequest)) {
    return {
      ok: false,
      title: "Contributor-rights gate is not activated",
      summary: "Set .github/contribution-rights.json effectiveAfterPullRequest to the bootstrap PR number before merge.",
    };
  }

  if (pr.number <= config.effectiveAfterPullRequest) {
    return {
      ok: true,
      title: "Grandfathered pull request",
      summary: `PR #${pr.number} predates contributor-rights enforcement after PR #${config.effectiveAfterPullRequest}.`,
    };
  }

  const selected = selectedRights(pr.body || "");
  if (selected.length !== 1) {
    return {
      ok: false,
      title: "Select exactly one contribution-rights path",
      summary: `Found ${selected.length} selected paths. Edit the Contribution rights section of the PR description and select exactly one.`,
    };
  }

  const decisions = comments
    .map((comment) => parseDecision(comment, config, pr))
    .filter(Boolean)
    .sort((a, b) => a.order - b.order);
  const latestReview = [...decisions].reverse().find((decision) => decision.decision === "review");
  const currentHeadDecisions = decisions.filter((decision) => decision.head === pr.head.sha.toLowerCase());
  const latestCurrent = currentHeadDecisions.at(-1);
  const unresolvedReview = latestReview && (!latestCurrent || latestCurrent.order <= latestReview.order || latestCurrent.decision === "review");

  if (selected[0] === "uncertain") {
    return {
      ok: false,
      title: "Rights review requested",
      summary: "Technical review may continue, but merge is blocked. Vexa will help determine whether the independent or corporate path applies.",
    };
  }

  if (selected[0] === "independent") {
    if (unresolvedReview) {
      return {
        ok: false,
        title: "Rights review is unresolved",
        summary: "A designated verifier opened a rights review. A current-head cleared decision is required before merge.",
      };
    }
    return {
      ok: true,
      title: "Independent contribution path is complete",
      summary: "The contributor selected the independent path. The separately required DCO check validates per-commit sign-offs.",
    };
  }

  if (latestCurrent?.decision === "verified" && !unresolvedReview) {
    return {
      ok: true,
      title: "Corporate contribution authorization verified",
      summary: `Receipt ${latestCurrent.receipt} was verified by @${latestCurrent.login} for PR #${pr.number} at head ${pr.head.sha}.`,
    };
  }

  const staleVerification = [...decisions].reverse().find((decision) => decision.decision === "verified");
  return {
    ok: false,
    title: staleVerification ? "Corporate authorization must be re-bound to the current head" : "Corporate authorization is pending",
    summary: staleVerification
      ? `The latest verified receipt covers ${staleVerification.head}, not current head ${pr.head.sha}.`
      : "Technical review may continue, but merge requires a designated verifier's current-head receipt decision.",
  };
}

async function requestJson(url, token, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!response.ok) throw new Error(`GitHub API ${response.status} for ${url}: ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

async function pullRequestsForEvent(event, apiBase, token) {
  if (event.pull_request) return [event.pull_request];
  if (event.issue?.pull_request) {
    return [await requestJson(`${apiBase}/repos/${event.repository.full_name}/pulls/${event.issue.number}`, token)];
  }
  if (event.merge_group?.head_ref) {
    const match = event.merge_group.head_ref.match(/(?:^|\/)gh-readonly-queue\/.+\/pr-(\d+)-/);
    if (!match) throw new Error(`Unrecognized merge-group head ref: ${event.merge_group.head_ref}`);
    return [await requestJson(`${apiBase}/repos/${event.repository.full_name}/pulls/${Number(match[1])}`, token)];
  }
  return [];
}

async function commentsForPullRequest(event, pr, apiBase, token) {
  const comments = [];
  for (let page = 1; page <= 20; page += 1) {
    const batch = await requestJson(
      `${apiBase}/repos/${event.repository.full_name}/issues/${pr.number}/comments?per_page=100&page=${page}`,
      token,
    );
    comments.push(...batch);
    if (batch.length < 100) break;
  }
  return comments;
}

async function createCheck(event, headSha, { name, ok, title, summary }, apiBase, token) {
  await requestJson(`${apiBase}/repos/${event.repository.full_name}/check-runs`, token, {
    method: "POST",
    body: JSON.stringify({
      name,
      head_sha: headSha,
      status: "completed",
      conclusion: ok ? "success" : "failure",
      output: { title, summary: summary.slice(0, 65000) },
    }),
  });
  return ok;
}

async function publishRightsCheck(event, headSha, results, apiBase, token) {
  const ok = results.every((result) => result.verdict.ok);
  const summary = results
    .map(({ pr, verdict }) => `### PR #${pr.number}: ${verdict.title}\n\n${verdict.summary}`)
    .join("\n\n");
  return createCheck(event, headSha, {
    name: "contribution-rights",
    ok,
    title: ok ? "Contribution rights complete" : "Contribution rights action required",
    summary,
  }, apiBase, token);
}

async function publishDcoPolicyCheck(event, apiBase, token) {
  const dco = event.check_run;
  const ordinarySuccess = dco.conclusion === "success" && dco.output?.summary?.trim() === "All commits are signed off!";
  return createCheck(event, dco.head_sha, {
    name: "dco-no-override",
    ok: ordinarySuccess,
    title: ordinarySuccess ? "DCO completed without override" : "DCO result is not an ordinary verified success",
    summary: ordinarySuccess
      ? "The maintained DCO App verified every applicable commit."
      : "Vexa does not accept manual DCO overrides or unknown success messages. The original author must complete DCO remediation and rerun the DCO check.",
  }, apiBase, token);
}

export async function run({ event, config, token, apiBase = "https://api.github.com" }) {
  if (event.check_run?.name === "DCO" && event.check_run?.app?.slug === "dco") {
    return publishDcoPolicyCheck(event, apiBase, token);
  }
  const pullRequests = await pullRequestsForEvent(event, apiBase, token);
  if (!pullRequests.length) return true;
  const results = [];
  for (const pr of pullRequests) {
    const comments = await commentsForPullRequest(event, pr, apiBase, token);
    results.push({ pr, verdict: evaluatePullRequest(pr, comments, config) });
  }
  const headSha = event.merge_group?.head_sha || pullRequests[0].head.sha;
  return publishRightsCheck(event, headSha, results, apiBase, token);
}

async function main() {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
  const config = JSON.parse(readFileSync(process.env.CONTRIBUTION_RIGHTS_CONFIG || ".github/contribution-rights.json", "utf8"));
  const ok = await run({ event, config, token: process.env.GITHUB_TOKEN });
  if (!ok) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
