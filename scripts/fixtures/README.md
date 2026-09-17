# scripts/fixtures — recorded inputs for the gate tests

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

Each file here is a frozen sample of a real API payload, read by a `scripts/*.test.mjs` suite so a
gate's parser can be proven against data instead of a mock.

| File | Read by | Shape |
|---|---|---|
| `merge-card-human-test.json` | `merge-card-gate.test.mjs` | `{ pr, comments }` — the `repos/:repo/issues/:n/comments` shape. Seven comments, of which exactly one qualifies as a human test: the others are the author validating their own PR, a bot, and three malformed attempts. |
| `merge-card-issue-comment-event.json` | `merge-card-gate.test.mjs` | `{ onPullRequest, onIssue }` — two `issue_comment` webhook payloads: `onPullRequest` is a comment on PR #4242 with `issue.pull_request`; `onIssue` is a comment on plain issue #4243 without it. |
| `merge-card-roster.json` | `merge-card-gate.test.mjs` | Synthetic API-shaped `{ roster, external, member, approvalCases, validationCases, rightsBodies }`: 6 roster logins; 2 PR cases with 1 review and 1 comment each; 6 approval cases (9 reviews); 6 validation cases (8 comments); 5 rights bodies. |

A fixture is edited only alongside the test that reads it: the counts above are asserted.
