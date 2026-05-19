# Stage: idle

| field        | value                                                |
|--------------|------------------------------------------------------|
| Actor        | mechanical                                           |
| Objective    | Dormant between release cycles.                      |
| Inputs       | (none)                                               |
| Outputs      | (none)                                               |

## Steps
1. Do nothing. Wait for a human (or scheduled cron) to start a new cycle.

## Exit
New cycle begins → enter `groom`. Bootstrap the release's worktree first
(`make release-worktree ID=<id>`) so N releases can run in parallel from
one clone without colliding on `.current-stage` / `.state/` / infra labels
(#229). The worktree lands at `../vexa-<id>` on branch `release/<id>`,
pre-seeded at `idle`.

## May NOT
- Any release work (code, infra, tests, reports).

## Next
`groom` — on a human decision to start a new cycle. Run `make release-groom
ID=<id>` from inside the per-release worktree, not from the main checkout.

## AI operating context
You are in `idle`. There is no active release. Your only legal action is to
help the user start a new cycle, which always begins with a per-release
worktree: `make release-worktree ID=<id>` from the main checkout, then
`cd ../vexa-<id> && make release-groom ID=<id>`. If asked to do anything
else, refuse: "There is no active release. Bootstrap one via `make
release-worktree`."
