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
7. Write `releases/<id>/groom.md` with **two layers, product first**:

   **(A) Product framing — at the TOP, before any technical pack detail.**
   This is what flows through to scope.yaml summary, PR descriptions,
   CHANGELOG, and ship-time release notes. Required sections:
   - **Elevator pitch** — one sentence a non-engineer can repeat.
   - **What we deliver** — 3-5 user-visible changes; each bullet names
     the change + the WHY (what breaks today, what shipping fixes).
   - **Who wins** — list of personas (self-hosters, API integrators,
     hosted users, Vexa team, …) × the delta each experiences.
   - **Who sees no change** — explicit list, to ground expectations
     and pre-empt "what about…?" scope creep.

   **(B) Technical pack detail — beneath (A).** Signal sources table,
   per-pack sections (symptom, scope shape, estimate, confidence,
   feature owner, open questions), suggested cycle shapes, approvals
   block at bottom.

   If signal is thin or packs are architectural (not fire-driven),
   (A) carries MORE weight, not less — the whole point is that
   engineers + PMs + users share one narrative for *why this cycle
   exists*. Never draft (B) without (A).
8. HALT. Present packs to human. Human picks which packs land in this cycle.

## Exit
`releases/<id>/groom.md` exists AND human has marked at least one pack with `approved: true`.

## May NOT
- Write `scope.yaml` (that's the `plan` stage).
- Edit code.
- Touch infra.
- Invent synthetic issues to fill packs.
- Skip the product framing layer (§7 A). A groom.md that is
  pack-detail-only is incomplete; plan + ship downstream both pull
  the product narrative from here, and rewriting it later as an
  afterthought never lands the same clarity. If the narrative feels
  hollow, stop and ask: *what are we actually delivering, and to whom?*

## Next
`plan` — once human approves packs.

## AI operating context
You are in `groom`. Your objective is to cluster open market signal
into issue packs a human can pick from — AND to frame the cycle as
a product narrative the whole org can read. Lead `groom.md` with
**what we deliver / who wins / who sees no change** (§7 A), then
justify the packs beneath. Both layers are required; neither alone
suffices. Do NOT write `scope.yaml` yourself (that's `plan`'s job);
do NOT edit code (that's `develop`'s). If asked: "I am in groom; I
may not write scope or edit code. After you pick packs, we'll
advance to plan."
