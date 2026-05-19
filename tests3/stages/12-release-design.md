# Stage: release-design

**Level:** release · **Role:** design · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | human + AI |
| Objective    | Decide the public release shape before publishing artifacts. |
| Inputs       | stage-signed artifact, code review, canonical validation, release doc. |
| Outputs      | release publication plan and production handoff draft. |

## Steps

1. `lib/stage.py assert-is release-design`.
2. Confirm version, semver/tag name, release-note shape, and artifact list.
3. Confirm DockerHub/package publication plan.
4. Draft production handoff expectations without performing production rollout.

## Exit

The public release can be delivered mechanically.

## May NOT

- Merge, tag, or publish yet.
- Treat production deploy as part of this repo's stage machine.

## Next

`release-deliver`.
