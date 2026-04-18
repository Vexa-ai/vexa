# Stage: groom

| field        | value                                                     |
|--------------|-----------------------------------------------------------|
| Actor        | AI                                                        |
| Objective    | Cluster market signal (GitHub + Discord) → issue packs.   |
| Inputs       | GitHub issues, Discord messages, internal notes           |
| Outputs      | `tests3/releases/<id>/groom.md` — candidate issue packs   |

## Steps
1. `lib/stage.py assert-is groom` — halt if wrong stage.
2. Fetch open GitHub issues (`gh issue list --state open --json number,title,labels,body`).
3. Fetch recent Discord reports (via the in-repo fetcher — §4.2 moves it into repo).
4. Read internal notes / triage log from prior releases.
5. Cluster by theme (bot lifecycle, webhooks, DB, transcription, …).
6. Draft one issue pack per cluster with: *symptom*, *owner feature(s)*, *estimated scope*, *confidence that it's reproducible*.
7. Write `releases/<id>/groom.md` — one section per pack.
8. HALT. Present packs to human. Human picks which packs land in this cycle.

## Exit
`releases/<id>/groom.md` exists AND human has marked at least one pack with `approved: true`.

## May NOT
- Write `scope.yaml` (that's the `plan` stage).
- Edit code.
- Touch infra.
- Invent synthetic issues to fill packs.

## Next
`plan` — once human approves packs.

## AI operating context
You are in `groom`. Your objective is to cluster open market signal into issue packs a human can pick from. Do NOT write `scope.yaml` yourself; that's `plan`'s job. Do NOT edit code; that's `develop`'s. If asked: "I am in groom; I may not write scope or edit code. After you pick packs, we'll advance to plan."
