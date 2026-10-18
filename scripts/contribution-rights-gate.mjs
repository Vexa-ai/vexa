#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const RIGHTS = ["independent", "corporate", "uncertain"];
const DECISION_MARKER = "<!-- vexa-contribution-rights-decision:v1 -->";
const RECEIPT_PATTERN = /^VCR-[0-9]{4}-[0-9]{4,}$/;
const SEARCHABLE_LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

// The rights marker may sit on the checkbox line itself, or -- as the repository's own pull-request
// template writes it -- on a continuation line of the same list item. Locate the marker, then walk
// back to the checkbox of the item it belongs to. Matching only the marker's own line silently
// reports zero selections for every correctly filled template.
export function selectedRights(body = "") {
  // Parse ONLY the declaration section. A body may legitimately MENTION these markers -- quoting the
  // template, or documenting this gate itself -- and matching the first occurrence anywhere leaves
  // such a body unable to declare anything, because the prose sits above the real checkbox. The
  // gate's own error message already points at "the Contribution rights section"; read exactly that.
  const all = body.split("\n");
  const heading = all.reduce((last, line, i) => (/^#{1,6}\s+contribution\s+rights\b/i.test(line) ? i : last), -1);
  const lines = heading >= 0 ? all.slice(heading) : all;
  return RIGHTS.filter((right) => {
    const marker = `<!-- rights:${right} -->`;
    const markerIndex = lines.findIndex((candidate) => candidate.includes(marker));
    if (markerIndex === -1) return false;
    for (let index = markerIndex; index >= 0; index -= 1) {
      const checkbox = lines[index].match(/^\s*-\s*\[([ xX])\]/);
      if (checkbox) return checkbox[1].toLowerCase() === "x";
      // A blank line ends the list item; never attribute a marker to an earlier item.
      if (!lines[index].trim()) return false;
    }
    return false;
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
  if (decision === "verified" && !RECEIPT_PATTERN.test(receipt || "")) return null;

  return {
    decision,
    head,
    receipt,
    login: comment.user.login,
    order: Number(comment.id || 0) || Date.parse(comment.created_at || comment.updated_at || 0),
  };
}

function decisionState(comments, config, pr) {
  const decisions = comments
    .map((comment) => parseDecision(comment, config, pr))
    .filter(Boolean)
    .sort((a, b) => a.order - b.order);
  const latestReview = [...decisions].reverse().find((decision) => decision.decision === "review");
  const currentHeadDecisions = decisions.filter((decision) => decision.head === pr.head.sha.toLowerCase());
  const latestCurrent = currentHeadDecisions.at(-1);
  const unresolvedReview = latestReview && (!latestCurrent || latestCurrent.order <= latestReview.order || latestCurrent.decision === "review");
  return { decisions, latestCurrent, unresolvedReview };
}

// Rights are declared once per contributor, not once per pull request. A contributor's standing
// comes from the maintained registry in .github/contribution-rights.json, or -- when they are not
// listed -- from an earlier merged PR of theirs that carried their own independent declaration.
// Corporate standing comes only from the registry: a per-PR receipt never carries forward.
export function registeredStanding(login, config) {
  if (!login) return null;
  const match = Object.entries(config.contributors || {}).find(([name]) => name.toLowerCase() === login.toLowerCase());
  if (!match) return null;
  const [name, entry] = match;
  if (entry?.path === "independent") {
    return { login: name, path: "independent", via: "registry", declared: entry.declared, source: entry.source };
  }
  if (entry?.path === "corporate" && RECEIPT_PATTERN.test(entry.receipt || "")) {
    return { login: name, path: "corporate", via: "registry", declared: entry.declared, source: entry.source, receipt: entry.receipt };
  }
  return null; // A malformed entry grants nothing.
}

// The independent declaration an earlier MERGED pull request carried: exactly one box, the
// independent one, with no unresolved rights review. A corporate receipt is bound to the PR and head
// it names, so it never carries forward; a continuing authorization belongs in the registry.
export function mergedDeclaration(earlier, comments, config) {
  const selected = selectedRights(earlier.body || "");
  if (selected.length !== 1 || selected[0] !== "independent") return null;
  const decisions = comments
    .map((comment) => parseDecision(comment, config, earlier))
    .filter(Boolean)
    .sort((a, b) => a.order - b.order);
  const latestReview = [...decisions].reverse().find((decision) => decision.decision === "review");
  if (latestReview && !decisions.some((decision) => decision.order > latestReview.order)) return null;
  return { path: "independent", via: "earlier-pr", pr: { number: earlier.number, url: earlier.html_url || earlier.url } };
}

// Whether a PR body is provably the author's own words: the GraphQL userContentEdits history lists
// every edit, including the creation, and each must be by the author. Any other editor -- a
// maintainer ticking the box on the author's behalf -- or a history that cannot be read in full
// disqualifies the PR as a source.
export function editedOnlyByAuthor(pullRequest, login) {
  const edits = pullRequest?.userContentEdits;
  if (!login || pullRequest?.author?.login?.toLowerCase() !== login.toLowerCase()) return false;
  if (!edits || !Array.isArray(edits.nodes) || edits.totalCount > edits.nodes.length) return false;
  return edits.nodes.every((edit) => edit?.editor?.login?.toLowerCase() === login.toLowerCase());
}

function describeStanding(standing) {
  if (standing.via === "registry") {
    const receipt = standing.path === "corporate" ? ` with receipt ${standing.receipt}` : "";
    return `@${standing.login} is registered in .github/contribution-rights.json as ${standing.path}${receipt} (declared ${standing.declared || "date not recorded"}; source: ${standing.source || "not recorded"}).`;
  }
  return `@${standing.login} declared the ${standing.path} path on merged PR #${standing.pr.number} (${standing.pr.url}).`;
}

function declarationVerdict(selection, state, pr) {
  if (selection === "uncertain") {
    return {
      ok: false,
      title: "Rights review requested",
      summary: "Technical review may continue, but merge is blocked. Vexa will help determine whether the independent or corporate path applies.",
    };
  }

  if (selection === "independent") {
    if (state.unresolvedReview) {
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

  if (state.latestCurrent?.decision === "verified" && !state.unresolvedReview) {
    return {
      ok: true,
      title: "Corporate contribution authorization verified",
      summary: `Receipt ${state.latestCurrent.receipt} was verified by @${state.latestCurrent.login} for PR #${pr.number} at head ${pr.head.sha}.`,
    };
  }

  const staleVerification = [...state.decisions].reverse().find((decision) => decision.decision === "verified");
  return {
    ok: false,
    title: staleVerification ? "Corporate authorization must be re-bound to the current head" : "Corporate authorization is pending",
    summary: staleVerification
      ? `The latest verified receipt covers ${staleVerification.head}, not current head ${pr.head.sha}.`
      : "Technical review may continue, but merge requires a designated verifier's current-head receipt decision.",
  };
}

// Whether run() must read the author's standing from GitHub: no per-PR selection, or a corporate
// selection by an author whose corporate authorization is registered. Any other selection is
// decided by the PR alone.
export function needsStanding(pr, config) {
  if (!Number.isInteger(config.effectiveAfterPullRequest) || pr.number <= config.effectiveAfterPullRequest) return false;
  const selected = selectedRights(pr.body || "");
  if (selected.length === 0) return true;
  return selected.length === 1 && selected[0] === "corporate" && registeredStanding(pr.user?.login, config)?.path === "corporate";
}

// evidence (gathered by run()): { authorStanding, coAuthors: [{ login, standing }], error }.
export function evaluatePullRequest(pr, comments, config, evidence = {}) {
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
  if (selected.length > 1) {
    return {
      ok: false,
      title: "Select exactly one contribution-rights path",
      summary: `Found ${selected.length} selected paths. Edit the Contribution rights section of the PR description and select exactly one.`,
    };
  }

  const state = decisionState(comments, config, pr);
  const author = pr.user?.login;
  const standing = registeredStanding(author, config) || evidence.authorStanding || null;
  const selection = selected[0];

  // An explicit selection decides this PR unless it repeats a corporate standing already on file;
  // even then, the per-PR receipt path stays open if the standing cannot carry the PR.
  if (selection && !(selection === "corporate" && standing?.path === "corporate")) {
    return declarationVerdict(selection, state, pr);
  }
  const verdict = standingVerdict(standing, state, evidence, author);
  return selection && !verdict.ok ? declarationVerdict(selection, state, pr) : verdict;
}

function standingVerdict(standing, state, evidence, author) {
  if (evidence.error) {
    return {
      ok: false,
      title: "Contributor standing could not be read",
      summary: `${evidence.error}. Re-run the check, or select exactly one path in the Contribution rights section.`,
    };
  }

  if (!standing) {
    return {
      ok: false,
      title: "Select exactly one contribution-rights path",
      summary: `${author ? `@${author} is not registered and has no earlier merged PR with a declaration` : "The PR author has no recorded declaration"}. ` +
        "This is needed once per contributor: edit the Contribution rights section of the PR description and select exactly one. Later PRs will not ask again.",
    };
  }

  if (!Array.isArray(evidence.coAuthors)) {
    return {
      ok: false,
      title: "Commit authors were not read",
      summary: "The author's standing covers only their own commits, and this run did not read the PR's commit authors. Re-run the check.",
    };
  }

  const uncovered = evidence.coAuthors.filter((coAuthor) => !coAuthor.standing).map((coAuthor) => `@${coAuthor.login}`);
  if (uncovered.length) {
    return {
      ok: false,
      title: "Commits by an undeclared author need a declaration",
      summary: `Commits in this PR are authored by ${uncovered.join(", ")}, who is not registered and has no earlier merged declaration. ` +
        `${describeStanding(standing)} That standing does not cover another person's commits: select exactly one path in the Contribution rights section.`,
    };
  }

  if (state.unresolvedReview) {
    return {
      ok: false,
      title: "Rights review is unresolved",
      summary: `${describeStanding(standing)} A designated verifier opened a rights review on this PR. A current-head cleared or verified decision is required before merge.`,
    };
  }

  return {
    ok: true,
    title: standing.path === "corporate" ? "Corporate authorization on file" : "Independent contributor on file",
    summary: `${describeStanding(standing)} No per-PR selection is needed. The separately required DCO check validates per-commit sign-offs.`,
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

async function commentsForPullRequest(repo, number, apiBase, token) {
  const comments = [];
  for (let page = 1; page <= 20; page += 1) {
    const batch = await requestJson(`${apiBase}/repos/${repo}/issues/${number}/comments?per_page=100&page=${page}`, token);
    comments.push(...batch);
    if (batch.length < 100) break;
  }
  return comments;
}

// GitHub logins of the PR's commit authors. A commit whose email is linked to no GitHub account has
// no login; its identity is covered by the separately required DCO check, not by this gate. The PR
// commits endpoint stops at 250, so a larger PR is read through the paginated compare endpoint.
async function commitAuthorLogins(repo, pr, apiBase, token) {
  const logins = new Set();
  const large = Number(pr.commits) > 250;
  for (let page = 1; page <= 30; page += 1) {
    const url = large
      ? `${apiBase}/repos/${repo}/compare/${pr.base.sha}...${pr.head.sha}?per_page=100&page=${page}`
      : `${apiBase}/repos/${repo}/pulls/${pr.number}/commits?per_page=100&page=${page}`;
    const response = await requestJson(url, token);
    const batch = large ? response.commits || [] : response;
    for (const commit of batch) if (commit.author?.login) logins.add(commit.author.login);
    if (batch.length < 100) return [...logins];
  }
  throw new Error(`PR #${pr.number} has more commits than this check reads`);
}

const EDIT_HISTORY_QUERY = `query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) { author { login } userContentEdits(first: 100) { totalCount nodes { editor { login } } } }
  }
}`;

async function bodyEditedOnlyByAuthor(repo, number, login, apiBase, token) {
  const [owner, name] = repo.split("/");
  const result = await requestJson(`${apiBase}/graphql`, token, {
    method: "POST",
    body: JSON.stringify({ query: EDIT_HISTORY_QUERY, variables: { owner, name, number } }),
  });
  return editedOnlyByAuthor(result?.data?.repository?.pullRequest, login);
}

// The earliest merged PR by `login` in this repository that carried their own independent declaration.
async function earlierDeclaration(repo, login, currentNumber, config, apiBase, token) {
  if (!SEARCHABLE_LOGIN.test(login)) return null;
  const query = encodeURIComponent(`repo:${repo} is:pr is:merged author:${login}`);
  const result = await requestJson(`${apiBase}/search/issues?q=${query}&sort=created&order=asc&per_page=50`, token);
  for (const item of result.items || []) {
    if (item.number === currentNumber || !item.pull_request?.merged_at) continue;
    if (item.user?.login?.toLowerCase() !== login.toLowerCase()) continue;
    if (!mergedDeclaration(item, [], config)) continue;
    const declaration = mergedDeclaration(item, await commentsForPullRequest(repo, item.number, apiBase, token), config);
    if (declaration && await bodyEditedOnlyByAuthor(repo, item.number, login, apiBase, token)) return { ...declaration, login };
  }
  return null;
}

async function standingOf(repo, login, currentNumber, config, apiBase, token) {
  return registeredStanding(login, config) || earlierDeclaration(repo, login, currentNumber, config, apiBase, token);
}

export async function gatherEvidence(repo, pr, config, apiBase, token) {
  const author = pr.user?.login;
  try {
    const authorStanding = author ? await standingOf(repo, author, pr.number, config, apiBase, token) : null;
    if (!authorStanding) return { authorStanding: null, coAuthors: [] };
    const coAuthors = [];
    for (const login of await commitAuthorLogins(repo, pr, apiBase, token)) {
      if (login.toLowerCase() === author.toLowerCase()) continue;
      coAuthors.push({ login, standing: await standingOf(repo, login, pr.number, config, apiBase, token) });
    }
    return { authorStanding, coAuthors };
  } catch (error) {
    return { error: `Reading contributor standing failed: ${error.message.slice(0, 300)}` };
  }
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
  const repo = event.repository.full_name;
  for (const pr of pullRequests) {
    results.push({ pr, ...(await verdictFor(repo, pr, config, apiBase, token)) });
  }
  // RE-READ BEFORE PUBLISHING. Runs for one pull request (its own events, each comment on it) can run in
  // parallel and finish in any order, so a run that read the state before a verifier's decision could
  // publish last and leave a stale verdict on the head. Each run therefore reads the pull request and
  // its comments again just before it publishes, and evaluates again if anything moved: whichever run
  // publishes last has read the latest state.
  for (let round = 0; round < 3; round++) {
    let moved = false;
    for (const r of results) {
      if (r.seen === null || !r.pr?.number) continue;
      let fresh, comments;
      try {
        fresh = await requestJson(`${apiBase}/repos/${repo}/pulls/${r.pr.number}`, token);
        comments = await commentsForPullRequest(repo, r.pr.number, apiBase, token);
      } catch {
        continue;
      }
      if (!fresh?.head?.sha || stateKey(fresh, comments) === r.seen) continue;
      Object.assign(r, { pr: fresh }, await verdictFor(repo, fresh, config, apiBase, token));
      moved = true;
    }
    if (!moved) break;
  }
  const headSha = event.merge_group?.head_sha || results[0].pr.head.sha;
  return publishRightsCheck(event, headSha, results, apiBase, token);
}

// What a verdict was computed from: the head, the body and every comment's identity and last edit.
function stateKey(pr, comments) {
  return JSON.stringify([pr.head?.sha, pr.body ?? "",
    (comments || []).map((c) => [c.id, c.updated_at ?? c.created_at ?? null, (c.body ?? "").length])]);
}

// A read that fails while evaluating a PR is that PR's verdict: the check fails closed on its head and
// says why, instead of leaving an earlier verdict standing. → { verdict, seen } (`seen` is null when
// the state could not be read).
async function verdictFor(repo, pr, config, apiBase, token) {
  try {
    const comments = await commentsForPullRequest(repo, pr.number, apiBase, token);
    const evidence = needsStanding(pr, config) ? await gatherEvidence(repo, pr, config, apiBase, token) : {};
    return { verdict: evaluatePullRequest(pr, comments, config, evidence), seen: stateKey(pr, comments) };
  } catch (error) {
    return {
      seen: null,
      verdict: {
        ok: false,
        title: "Contribution rights could not be evaluated",
        summary: `Reading GitHub failed, so this check fails closed. Any new comment, edit or push re-runs it.\n\n${error.message}`,
      },
    };
  }
}

// The verdict is the check run published on the evaluated head, never this job's own status:
// issue_comment and check_run runs belong to the default branch's newest commit, so a failing job
// would mark that commit red for another PR's state.
async function main() {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"));
  const config = JSON.parse(readFileSync(process.env.CONTRIBUTION_RIGHTS_CONFIG || ".github/contribution-rights.json", "utf8"));
  const ok = await run({ event, config, token: process.env.GITHUB_TOKEN });
  console.log(ok ? "Published a passing verdict." : "Published a failing verdict on the evaluated head.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    console.log(`::error title=contribution-rights not published::${String(error.message).split("\n")[0]}`);
  });
}
