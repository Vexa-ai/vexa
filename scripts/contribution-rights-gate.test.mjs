import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { evaluatePullRequest, mergedDeclaration, registeredStanding, run } from "./contribution-rights-gate.mjs";

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

// --- Declared once per contributor, not per PR (founder ruling 2026-10-02) ---

const registry = {
  ...config,
  contributors: {
    "Known-Dev": { path: "independent", declared: "2026-08-14", source: "standing declaration" },
    "corp-dev": { path: "corporate", declared: "2026-09-01", source: "employer letter", receipt: "VCR-2026-0042" },
    "broken-corp": { path: "corporate", declared: "2026-09-01", source: "missing receipt" },
  },
};
const authored = (login, overrides = {}) => pr({ body: body("none"), user: { login }, ...overrides });
const noCoAuthors = { coAuthors: [] };

test("a registered independent author passes with no box ticked", () => {
  const verdict = evaluatePullRequest(authored("known-dev"), [], registry, noCoAuthors);
  assert.equal(verdict.ok, true);
  assert.match(verdict.summary, /registered in \.github\/contribution-rights\.json as independent/);
  assert.match(verdict.summary, /No per-PR selection is needed/);
});

test("a registered corporate author passes citing the stored receipt", () => {
  const verdict = evaluatePullRequest(authored("corp-dev"), [], registry, noCoAuthors);
  assert.equal(verdict.ok, true);
  assert.match(verdict.summary, /VCR-2026-0042/);
  // Ticking the corporate box out of habit does not demand a fresh per-head receipt.
  assert.equal(evaluatePullRequest(authored("corp-dev", { body: body("corporate") }), [], registry, noCoAuthors).ok, true);
});

test("a malformed registry entry grants nothing", () => {
  assert.equal(registeredStanding("broken-corp", registry), null);
  assert.equal(evaluatePullRequest(authored("broken-corp"), [], registry, noCoAuthors).ok, false);
});

test("an explicit selection that differs from the standing decides this PR", () => {
  assert.equal(evaluatePullRequest(authored("known-dev", { body: body("uncertain") }), [], registry, noCoAuthors).ok, false);
  const corporate = evaluatePullRequest(authored("known-dev", { body: body("corporate") }), [], registry, noCoAuthors);
  assert.equal(corporate.ok, false);
  assert.match(corporate.title, /Corporate authorization is pending/);
});

test("an unregistered author with an earlier merged declaration passes, naming that PR", () => {
  const earlier = { number: 77, body: body("independent"), html_url: "https://github.com/Vexa-ai/vexa/pull/77" };
  const standing = { ...mergedDeclaration(earlier, [], config), login: "returning-dev" };
  const verdict = evaluatePullRequest(authored("returning-dev"), [], config, { authorStanding: standing, coAuthors: [] });
  assert.equal(verdict.ok, true);
  assert.match(verdict.summary, /merged PR #77/);
});

test("an earlier PR counts only with a valid declaration", () => {
  const earlier = (selected, extra = {}) => ({ number: 77, body: body(selected), html_url: "u", ...extra });
  const onEarlier = (fields) => {
    const comment = decision(fields);
    comment.body = comment.body.replace("PR: #101", "PR: #77");
    return comment;
  };
  assert.equal(mergedDeclaration(earlier("none"), [], config), null);
  assert.equal(mergedDeclaration(earlier("uncertain"), [], config), null);
  assert.equal(mergedDeclaration(earlier("corporate"), [], config), null, "corporate without a verifier receipt");
  assert.equal(mergedDeclaration(earlier("corporate"), [onEarlier({})], config).receipt, "VCR-2026-0001");
  assert.equal(mergedDeclaration(earlier("independent"), [onEarlier({ type: "review" })], config), null, "unresolved review");
  const cleared = [onEarlier({ type: "review", created: 1 }), onEarlier({ type: "cleared", receipt: "", created: 2 })];
  assert.equal(mergedDeclaration(earlier("independent"), cleared, config).path, "independent");
});

test("a first-time author still needs exactly one selection", () => {
  const none = evaluatePullRequest(authored("new-dev"), [], registry, { authorStanding: null, coAuthors: [] });
  assert.equal(none.ok, false);
  assert.match(none.title, /Select exactly one/);
  assert.match(none.summary, /needed once per contributor/);
  assert.equal(evaluatePullRequest(authored("new-dev", { body: body("independent") }), [], registry, {}).ok, true);
  const two = body("independent").replace("[ ] An employer", "[x] An employer");
  assert.equal(evaluatePullRequest(authored("known-dev", { body: two }), [], registry, noCoAuthors).ok, false);
});

test("uncertain still blocks", () => {
  assert.equal(evaluatePullRequest(authored("new-dev", { body: body("uncertain") }), [], registry, {}).ok, false);
});

test("an unresolved verifier review still blocks a registered author", () => {
  const review = decision({ type: "review", created: 1 });
  const held = evaluatePullRequest(authored("known-dev"), [review], registry, noCoAuthors);
  assert.equal(held.ok, false);
  assert.match(held.title, /unresolved/);
  assert.equal(evaluatePullRequest(authored("corp-dev"), [review], registry, noCoAuthors).ok, false);
  const cleared = decision({ type: "cleared", receipt: "", created: 2 });
  assert.equal(evaluatePullRequest(authored("known-dev"), [review, cleared], registry, noCoAuthors).ok, true);
});

test("an unregistered co-author's commits force the declaration path", () => {
  const evidence = { coAuthors: [{ login: "drive-by", standing: null }, { login: "corp-dev", standing: registeredStanding("corp-dev", registry) }] };
  const verdict = evaluatePullRequest(authored("known-dev"), [], registry, evidence);
  assert.equal(verdict.ok, false);
  assert.match(verdict.summary, /@drive-by/);
  assert.doesNotMatch(verdict.summary, /@corp-dev,/);
  // The declaration path is still open to the PR author.
  assert.equal(evaluatePullRequest(authored("known-dev", { body: body("independent") }), [], registry, evidence).ok, true);
});

test("a corporate selection the standing cannot carry falls back to the per-PR receipt", () => {
  const evidence = { coAuthors: [{ login: "drive-by", standing: null }] };
  const corporate = authored("corp-dev", { body: body("corporate") });
  assert.equal(evaluatePullRequest(corporate, [], registry, evidence).ok, false);
  assert.equal(evaluatePullRequest(corporate, [decision()], registry, evidence).ok, true);
});

test("standing fails closed when commit authors or standing were not read", () => {
  assert.equal(evaluatePullRequest(authored("known-dev"), [], registry, {}).ok, false);
  const failed = evaluatePullRequest(authored("known-dev"), [], registry, { error: "Reading contributor standing failed: 502" });
  assert.equal(failed.ok, false);
  assert.match(failed.summary, /502/);
});

test("grandfathering is unchanged", () => {
  assert.equal(evaluatePullRequest(authored("new-dev", { number: 100 }), [], registry, {}).ok, true);
  assert.equal(evaluatePullRequest(authored("known-dev", { number: 100 }), [], registry, {}).ok, true);
});

test("the shipped registry is well-formed and names the maintainer as independent", () => {
  const shipped = JSON.parse(readFileSync(new URL("../.github/contribution-rights.json", import.meta.url), "utf8"));
  for (const [login, entry] of Object.entries(shipped.contributors)) {
    assert.ok(registeredStanding(login, shipped), `invalid registry entry for ${login}`);
    assert.match(entry.declared, /^\d{4}-\d{2}-\d{2}$/, `declared date for ${login}`);
    assert.ok(entry.source, `source for ${login}`);
  }
  assert.equal(registeredStanding("dmitriyg228", shipped).path, "independent");
});

function mockGitHub(routes) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const route = routes.find(([pattern]) => pattern.test(url));
    const payload = route ? route[1] : {};
    return { ok: true, status: options.method === "POST" ? 201 : 200, json: async () => payload, text: async () => "" };
  };
  return { calls, restore: () => { globalThis.fetch = originalFetch; }, published: () => JSON.parse(calls.find((c) => c.url.endsWith("/check-runs")).options.body) };
}

test("run: a registered author's PR passes with no box after reading commit authors", async () => {
  const mock = mockGitHub([
    [/\/issues\/101\/comments/, []],
    [/\/pulls\/101\/commits/, [{ author: { login: "Known-Dev" } }, { author: null }]],
  ]);
  try {
    const event = { repository: { full_name: "Vexa-ai/vexa" }, pull_request: authored("known-dev") };
    assert.equal(await run({ event, config: registry, token: "t", apiBase: "https://example.test" }), true);
    assert.equal(mock.published().conclusion, "success");
    assert.ok(!mock.calls.some((c) => c.url.includes("/search/")), "a registered author needs no search");
  } finally {
    mock.restore();
  }
});

test("run: an unregistered author is found through an earlier merged PR", async () => {
  const mock = mockGitHub([
    [/\/issues\/101\/comments/, []],
    [/\/issues\/77\/comments/, []],
    [/\/search\/issues/, { items: [
      { number: 70, user: { login: "returning-dev" }, body: body("none"), pull_request: { merged_at: "2026-09-01T00:00:00Z" } },
      { number: 77, user: { login: "returning-dev" }, body: body("independent"), html_url: "https://github.com/Vexa-ai/vexa/pull/77", pull_request: { merged_at: "2026-09-02T00:00:00Z" } },
    ] }],
    [/\/pulls\/101\/commits/, [{ author: { login: "returning-dev" } }]],
  ]);
  try {
    const event = { repository: { full_name: "Vexa-ai/vexa" }, pull_request: authored("returning-dev") };
    assert.equal(await run({ event, config: registry, token: "t", apiBase: "https://example.test" }), true);
    assert.match(mock.published().output.summary, /merged PR #77/);
    const search = decodeURIComponent(mock.calls.find((c) => c.url.includes("/search/")).url);
    assert.match(search, /repo:Vexa-ai\/vexa is:pr is:merged author:returning-dev/);
  } finally {
    mock.restore();
  }
});

test("run: an unregistered co-author with no earlier declaration blocks the standing path", async () => {
  const mock = mockGitHub([
    [/\/issues\/101\/comments/, []],
    [/\/search\/issues/, { items: [] }],
    [/\/pulls\/101\/commits/, [{ author: { login: "known-dev" } }, { author: { login: "drive-by" } }]],
  ]);
  try {
    const event = { repository: { full_name: "Vexa-ai/vexa" }, pull_request: authored("known-dev") };
    assert.equal(await run({ event, config: registry, token: "t", apiBase: "https://example.test" }), false);
    assert.match(mock.published().output.summary, /@drive-by/);
  } finally {
    mock.restore();
  }
});

test("run: a PR beyond the 250-commit list is read through the compare endpoint", async () => {
  const commits = [...Array(100).fill({ author: { login: "known-dev" } })];
  const mock = mockGitHub([
    [/\/issues\/101\/comments/, []],
    [/\/compare\/.+page=1$/, { commits }],
    [/\/compare\/.+page=2$/, { commits: [{ author: { login: "drive-by" } }] }],
    [/\/search\/issues/, { items: [] }],
  ]);
  try {
    const big = authored("known-dev", { commits: 300, base: { sha: oldSha } });
    const event = { repository: { full_name: "Vexa-ai/vexa" }, pull_request: big };
    assert.equal(await run({ event, config: registry, token: "t", apiBase: "https://example.test" }), false);
    assert.match(mock.published().output.summary, /@drive-by/);
    assert.ok(!mock.calls.some((c) => c.url.includes("/pulls/101/commits")));
  } finally {
    mock.restore();
  }
});
