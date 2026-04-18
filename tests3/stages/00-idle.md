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
New cycle begins → enter `groom`.

## May NOT
- Any release work (code, infra, tests, reports).

## Next
`groom` — on a human decision to start a new cycle.

## AI operating context
You are in `idle`. There is no active release. Your only legal action is to
help the user decide whether to start a new cycle (`make release-groom`). If
asked to do anything else, refuse: "There is no active release. Start one via
`make release-groom`."
