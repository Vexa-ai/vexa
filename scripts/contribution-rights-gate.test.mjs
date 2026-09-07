import assert from "node:assert/strict";
import test from "node:test";

import { evaluatePullRequest, run } from "./contribution-rights-gate.mjs";

const sha = "a".repeat(40);
const oldSha = "b".repeat(40);
const config = { effectiveAfterPullRequest: 100, verifiers: ["rights-verifier"] };
const body = (selected) => `
## Contribution rights
- [${selected === "independent" ? "x" : " "}] I own this contribution. <!-- rights:independent -->
- [${selected === "corporate" ? "x" : " "}] An employer or client owns or controls it. <!-- rights:corporate -->
- [${selected === "uncertain" ? "x" : " "}] I am unsure. <!-- rights:uncertain -->
`;
const pr = (overrides = {}) => ({ number: 101, body: body("independent"), head: { sha }, ...overrides });
const decision = ({ type = "verified", head = sha, login = "rights-verifier", receipt = "VCR-2026-0001", created = 1 } = {}) => ({
  id: created,
  created_at: new Date(created * 1000).toISOString(),
  user: { login },
  body: `<!-- vexa-contribution-rights-decision:v1 -->
Decision: ${type}
Receipt: ${receipt}
PR: #101
Head: ${head}`,
});

test("grandfathers PRs at or before activation", () => {
  assert.equal(evaluatePullRequest(pr({ number: 100, body: "" }), [], config).ok, true);
});

test("fails closed while the bootstrap PR number is unset", () => {
  const verdict = evaluatePullRequest(pr(), [], { ...config, effectiveAfterPullRequest: "__BOOTSTRAP_PR__" });
  assert.equal(verdict.ok, false);
  assert.match(verdict.title, /not activated/);
});

test("requires exactly one declaration", () => {
  assert.equal(evaluatePullRequest(pr({ body: body("none") }), [], config).ok, false);
  const multiple = `${body("independent")}`.replace("[ ] An employer", "[x] An employer");
  assert.equal(evaluatePullRequest(pr({ body: multiple }), [], config).ok, false);
});

// Regression: the fixture above puts each marker on its checkbox line, but
// .github/PULL_REQUEST_TEMPLATE.md wraps the label and leaves the marker on a continuation line.
// Matching only the marker's own line reported zero selections for every correctly ticked PR, and
// because `contribution-rights` is not a required check the gate failed unnoticed on every pull
// request from activation until 2026-08-09.
const templateShapedBody = (selected) => `
## Contribution rights

- [${selected === "independent" ? "x" : " "}] **Independent:** I created this contribution, or otherwise have the right to submit it
  under Apache-2.0, and it is not owned or controlled by an employer, client, or other entity.
  <!-- rights:independent -->
- [${selected === "corporate" ? "x" : " "}] **Employer/client authorization required:** an employer, client, or other entity owns or
  may control this contribution. I am requesting Vexa's private corporate-authorization process.
  <!-- rights:corporate -->
- [${selected === "uncertain" ? "x" : " "}] **Unsure:** I need a private rights review before merge.
  <!-- rights:uncertain -->
`;

test("reads a declaration whose marker sits on a continuation line", () => {
  assert.equal(evaluatePullRequest(pr({ body: templateShapedBody("independent") }), [], config).ok, true);
  assert.equal(evaluatePullRequest(pr({ body: templateShapedBody("none") }), [], config).ok, false);
  const both = templateShapedBody("independent").replace("[ ] **Employer", "[x] **Employer");
  assert.equal(evaluatePullRequest(pr({ body: both }), [], config).ok, false);
});

test("does not attribute an orphaned marker to an earlier list item", () => {
  const orphan = "## Contribution rights\n\n- [x] Some other checked item\n\n  <!-- rights:independent -->\n";
  assert.equal(evaluatePullRequest(pr({ body: orphan }), [], config).ok, false);
});

test("a body that MENTIONS the markers can still declare", () => {
  // Found by the PR that fixed the line-shape bug failing its own gate: explaining the bug
  // required quoting the template, which put a marker occurrence above the declaration. Any
  // PR documenting this gate could not declare anything. Parse the declaration section only.
  const prose = [
    "## What broke",
    "The parser matched `<!-- rights:independent -->` wherever it appeared, including here:",
    "",
    "  <!-- rights:independent -->",
    "",
    "## Contribution rights",
    "- [x] I own this contribution. <!-- rights:independent -->",
    "- [ ] An employer or client owns or controls it. <!-- rights:corporate -->",
    "- [ ] I am unsure. <!-- rights:uncertain -->",
  ].join("\n");
  assert.equal(evaluatePullRequest(pr({ body: prose }), [], config).ok, true,
    "prose above the declaration swallowed the declaration");
});

test("independent path passes without a CLA", () => {
  const verdict = evaluatePullRequest(pr(), [], config);
  assert.equal(verdict.ok, true);
  assert.match(verdict.summary, /separately required DCO/);
});

test("uncertain path opens review and blocks merge", () => {
  assert.equal(evaluatePullRequest(pr({ body: body("uncertain") }), [], config).ok, false);
});

test("corporate path requires a designated current-head receipt", () => {
  const corporate = pr({ body: body("corporate") });
  assert.equal(evaluatePullRequest(corporate, [], config).ok, false);
  assert.equal(evaluatePullRequest(corporate, [decision({ login: "outsider" })], config).ok, false);
  assert.equal(evaluatePullRequest(corporate, [decision({ head: oldSha })], config).ok, false);
  assert.equal(evaluatePullRequest(corporate, [decision()], config).ok, true);
});

test("verifier identity is case-insensitive but receipt format is strict", () => {
  const corporate = pr({ body: body("corporate") });
  assert.equal(evaluatePullRequest(corporate, [decision({ login: "RIGHTS-VERIFIER" })], config).ok, true);
  assert.equal(evaluatePullRequest(corporate, [decision({ receipt: "sony-email" })], config).ok, false);
});

test("a decision for another PR cannot authorize this PR", () => {
  const wrongPr = decision();
  wrongPr.body = wrongPr.body.replace("PR: #101", "PR: #999");
  assert.equal(evaluatePullRequest(pr({ body: body("corporate") }), [wrongPr], config).ok, false);
});

test("a new push invalidates corporate verification", () => {
  const verdict = evaluatePullRequest(
    pr({ body: body("corporate"), head: { sha: oldSha } }),
    [decision({ head: sha })],
    config,
  );
  assert.equal(verdict.ok, false);
  assert.match(verdict.title, /re-bound/);
});

test("a review hold blocks an independent declaration until current-head clearance", () => {
  const review = decision({ type: "review", created: 1 });
  assert.equal(evaluatePullRequest(pr(), [review], config).ok, false);
  const cleared = decision({ type: "cleared", receipt: "", created: 2 });
  assert.equal(evaluatePullRequest(pr(), [review, cleared], config).ok, true);
  assert.equal(evaluatePullRequest(pr({ head: { sha: oldSha } }), [review, cleared], config).ok, false);
});

test("a review posted after corporate verification re-blocks merge", () => {
  const verified = decision({ created: 1 });
  const review = decision({ type: "review", created: 2 });
  assert.equal(evaluatePullRequest(pr({ body: body("corporate") }), [verified, review], config).ok, false);
});

test("verification after review resolves the corporate path at the same head", () => {
  const review = decision({ type: "review", created: 1 });
  const verified = decision({ created: 2 });
  assert.equal(evaluatePullRequest(pr({ body: body("corporate") }), [review, verified], config).ok, true);
});

test("markdown text cannot spoof an unchecked declaration marker", () => {
  const spoofed = `${body("none")}\nThe text [x] appears elsewhere <!-- rights:independent -->`;
  assert.equal(evaluatePullRequest(pr({ body: spoofed }), [], config).ok, false);
});

test("pull-request event publishes the result against the PR head", async () => {
  const event = { repository: { full_name: "Vexa-ai/vexa" }, pull_request: pr() };
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const payload = url.includes("/comments") ? [] : {};
    return { ok: true, status: options.method === "POST" ? 201 : 200, json: async () => payload, text: async () => "" };
  };
  try {
    assert.equal(await run({ event, config, token: "test", apiBase: "https://example.test" }), true);
    const publish = calls.find((call) => call.url.endsWith("/check-runs"));
    assert.equal(JSON.parse(publish.options.body).head_sha, sha);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("issue-comment event re-evaluates the current PR head", async () => {
  const event = {
    repository: { full_name: "Vexa-ai/vexa" },
    issue: { number: 101, pull_request: { url: "https://example.test/pr/101" } },
  };
  const corporate = pr({ body: body("corporate") });
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    let payload = {};
    if (url.endsWith("/pulls/101")) payload = corporate;
    else if (url.includes("/issues/101/comments")) payload = [decision()];
    return { ok: true, status: options.method === "POST" ? 201 : 200, json: async () => payload, text: async () => "" };
  };
  try {
    assert.equal(await run({ event, config, token: "test", apiBase: "https://example.test" }), true);
    const publish = calls.find((call) => call.url.endsWith("/check-runs"));
    assert.equal(JSON.parse(publish.options.body).conclusion, "success");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ordinary DCO App success produces the no-override success check", async () => {
  const event = {
    repository: { full_name: "Vexa-ai/vexa" },
    check_run: { name: "DCO", app: { slug: "dco" }, head_sha: sha, conclusion: "success", output: { summary: "All commits are signed off!" } },
  };
  const originalFetch = globalThis.fetch;
  let published;
  globalThis.fetch = async (_url, options = {}) => {
    published = JSON.parse(options.body);
    return { ok: true, status: 201, json: async () => ({}), text: async () => "" };
  };
  try {
    assert.equal(await run({ event, config, token: "test", apiBase: "https://example.test" }), true);
    assert.equal(published.name, "dco-no-override");
    assert.equal(published.conclusion, "success");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("DCO App manual override is rejected", async () => {
  const event = {
    repository: { full_name: "Vexa-ai/vexa" },
    check_run: { name: "DCO", app: { slug: "dco" }, head_sha: sha, conclusion: "success", output: { summary: "Commit sign-off was manually approved." } },
  };
  const originalFetch = globalThis.fetch;
  let published;
  globalThis.fetch = async (_url, options = {}) => {
    published = JSON.parse(options.body);
    return { ok: true, status: 201, json: async () => ({}), text: async () => "" };
  };
  try {
    assert.equal(await run({ event, config, token: "test", apiBase: "https://example.test" }), false);
    assert.equal(published.name, "dco-no-override");
    assert.equal(published.conclusion, "failure");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("merge-group run resolves its PR from the queue ref and publishes on the group head", async () => {
  const groupSha = "c".repeat(40);
  const event = {
    repository: { full_name: "Vexa-ai/vexa" },
    merge_group: { head_sha: groupSha, head_ref: "refs/heads/gh-readonly-queue/main/pr-101-abcdef" },
  };
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    let payload = {};
    if (url.endsWith("/pulls/101")) payload = pr();
    else if (url.includes("/issues/101/comments")) payload = [];
    return { ok: true, status: options.method === "POST" ? 201 : 200, json: async () => payload, text: async () => "" };
  };
  try {
    assert.equal(await run({ event, config, token: "test", apiBase: "https://example.test" }), true);
    const publish = calls.find((call) => call.url.endsWith("/check-runs"));
    assert.equal(JSON.parse(publish.options.body).head_sha, groupSha);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
