---
name: 7-human
description: "Invoke when the user needs the human-validation step of a Vexa release — generate the per-release human checklist (always-checks + scope-specific checks), fill it in while verifying the deployed system, then gate on every box being checked. Mandatory between stage 6 (full) and stage 8 (ship). Use when the user says 'generate the human sheet', 'what does the human need to verify', 'check the live deployment', 'sign off', 'human gate', or after `release-full` passes."
---

## Stage 7 of 9 — human validation

Stage 6 (full) proved the automated matrix is green. Stage 7 proves the product works as a human sees it — UI loads, real bot joins a real meeting, transcripts persist, nothing weird in the logs. Both gates must pass before `release-ship` will run.

## Commands

```bash
# (a) Generate the checklist — writes tests3/releases/<id>/human-checklist.md
make release-human-sheet SCOPE=$SCOPE

# (b) Edit the file, change `- [ ]` → `- [x]` as you verify each item

# (c) Gate-check
make release-human-gate SCOPE=$SCOPE
```

## What's in the checklist

**ALWAYS** — from `tests3/human-always.yaml`. Same every release:

- Lite: dashboard loads, session creation works, logs clean.
- Compose: dashboard loads, bot joins a real Google Meet, transcript appears live + persists after stop, logs clean.
- Helm: every pod Running, gateway + dashboard reachable via NodePort, no Warning events.
- Release integrity: all running images carry the same tag, no stale containers.

**THIS RELEASE** — from `scope.issues[].human_verify[]`. Per issue, the release author writes at stage 1 a list of `{mode, do, expect}` tuples. E.g.:

```yaml
human_verify:
  - mode: compose
    do: "PUT /user/webhook with webhook_events={meeting.completed:true, meeting.status_change:true}; POST /bots; DELETE within 5s"
    expect: "After 20s, meeting.data has webhook_delivery.status=delivered AND webhook_deliveries[] has >= 1 entry"
```

Each becomes one `- [ ]` item referencing the correct VM IP (auto-substituted from `.state-<mode>/`).

## Workflow

1. Run `make release-human-sheet SCOPE=$SCOPE`.
2. Open `tests3/releases/<id>/human-checklist.md`.
3. For each item: perform the action on the referenced deployment, confirm the expectation, change `- [ ]` to `- [x]`.
4. If something fails: add a note in the `## Issues found` section. Fix → commit → push → stage 5 (iterate) → stage 6 (full) → regenerate checklist with `--force`.
5. When everything is ticked, run `make release-human-gate SCOPE=$SCOPE`. It exits non-zero if any box is still empty.

## Why this gate can't be skipped

Automated tests prove the code does what we think it does. Human tests prove the product does what the user thinks it does. They catch different classes of bugs:

- **Automated catches**: HTTP status codes, JSON shape, HMAC signatures, DB pool behavior, consumer-group recovery.
- **Human catches**: dashboard UX regressions, magic-link emails formatted correctly, transcript rendering at scale, error messages that make sense, meeting-join behavior on the actual Google Meet UI (not a mock).

`release-ship` calls `release-human-gate` first; any unchecked box blocks merge.

## Tips

- Open all three VMs in side-by-side terminal panes before starting.
- For the "real meeting" item, keep a spare Google Meet URL handy.
- If a human check fails for a reason outside the release's scope (e.g. Google Meet UI changed), log it in `## Issues found` AND file a new GH issue — don't silently `- [x]` around it.

## Don't regenerate mid-validation

`release-human-sheet` refuses to overwrite an existing checklist by default. Use `--force` only after `release-full` has been re-run with a new fix — otherwise you lose in-progress checkmarks.

## Next

Once the human gate passes:
→ stage 8: `make release-ship SCOPE=$SCOPE`.
