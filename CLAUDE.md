@AGENTS.md

## Claude Code specifics

Layout: pnpm + turbo monorepo — `core/` (gateway, agent, runtime, identity, meetings),
`clients/terminal` (Next.js 15, port 3000, `npm run dev`), `docs/docs` (Mintlify site — the
published law), `calm/` (FINOS CALM model). Licensing is FINOS-gated: new deps must be
Category A (MIT/BSD/Apache); weak-copyleft needs an entry in `license-exceptions.json`
(ADR-0004) — never add GPL/AGPL.

## Never replace a pull-request or issue body wholesale

`gh pr edit --body-file` and `gh issue edit --body-file` overwrite everything, including the
*Contribution rights* checkbox and any verdict a human put there. On 2026-08-09 that wiped a ticked
box on #1095, reset `contribution-rights`, and the clobbered state was then reported back as fact —
sending a maintainer to redo something already done, in two separate sessions. Re-read the live body
and re-apply what a human set, or edit surgically. Same care for labels, reviews and approvals.

The rights declaration and the DCO sign-off are the human's alone (`CONTRIBUTOR_RIGHTS.md § Agent-assisted
contributions`): an agent may install and verify the sign-off mechanism and repair unsigned commits
*after* the human picks a path, and may never choose that path or sign for anyone.

## When a check contradicts what you can see, read the check

`contribution-rights` failed on every pull request from its activation until 2026-08-09 because
`scripts/contribution-rights-gate.mjs` tested the rights marker's own line for a checkbox, while
the template leaves the marker on a continuation line. Nobody noticed for months: it is not a
required check, so everything merged anyway, and the unit fixture encoded the same wrong assumption
it was meant to guard. A gate that disagrees with the visible state is a finding, not an obstacle —
and a fixture that mirrors the implementation instead of the real artifact proves nothing.
