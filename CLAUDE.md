# Claude Code — start here

This repo operates under a **strict stage state machine**. Before doing
*anything*, you must orient yourself.

## First action — ALWAYS, every session

```bash
python3 tests3/lib/stage.py probe
```

This prints the current stage + legal next stages + a one-line objective.

## Every message includes a two-line orientation header

Every assistant message MUST begin with two header lines, each ≤125 chars:

```
[stage: <name> · release: <id> · next: <legal-next> | <legal-next>]
[features (conf/gate): <abbrev> <conf>/<gate> · <abbrev> <conf>/<gate> · …]
```

Rules:
- **Line 1 (stage)** — verbatim from `stage.py probe`. Hard cap 125 chars; if legal-next would overflow, drop reverse-edge entries.
- **Line 2 (features)** — affected features per the current release's latest validate report (`tests3/reports/release-<ver>-<ts>.md`). Format `<abbrev> <conf>/<gate>` where conf and gate are integer percent. Hard cap 125 chars; show the features at greatest gate-shortfall first, abbreviate names (`bot-lifecycle`→`bot-life`, `post-meeting-transcription`→`post-mtg`, `security-hygiene`→`sec-hyg`, `meeting-urls`→`mtg-urls`, `dashboard`→`dash`, `infrastructure`→`infra`). Truncate the tail with `…` if overflow.

Why: the human reading a long thread can't constantly re-probe stage or
reopen the latest validate report. These two lines surface "where are we"
+ "where are we exposed" at one glance every turn.

## Obey the stage

Each stage has an explicit contract at `tests3/stages/NN-<name>.md`
(objective, inputs, outputs, exit condition, **may NOT** list). Read it
before taking any action. If the user asks for something outside the
current stage's `may NOT` list, **refuse** with a stage-aware message:

> *"Currently in `<stage>`; that action is forbidden (`<rule>`). To do it,
> transition via `<legal next stage>`."*

## Common situations — map to stage

| user says                                                    | likely stage     |
|--------------------------------------------------------------|------------------|
| "draft the scope / what code do we change?"                  | `plan-solution`  |
| "audit the proposal / any workarounds or fallbacks?"         | `plan-audit`     |
| "approve the plan"                                           | `plan-human`     |
| "write code / fix Y / update tests3 / update docs / local deploy / LOCAL validate" | `develop-code` |
| "audit the diff for security / workarounds / fallbacks / best practice" | `develop-audit` |
| "walk the local checklist against LOCAL stack"               | `develop-human`  |
| "run the canonical validate matrix on throwaway infra"       | `stage`          |
| "audit canonical deploy artefacts (helm, compose, infra)"    | `stage-audit`    |
| "code review + canonical-stack eyeroll"                      | `stage-human`    |
| "ship it / merge to main / tag / promote :latest"            | `release`        |
| "start a new release / groom issues"                         | `groom`          |

If the current stage doesn't match what the user asked for, **don't
bumble around trying to make it work**. State the mismatch and the
legal transition path.

## Why this matters

Three productive levels (plan / dev / stage), each repeating the same
inner-loop pattern `do → audit → human → next`, plus `release` (delivery)
and `teardown` (cleanup):

```
groom → plan-solution → plan-audit → plan-human
                              │           ▲
                              └───────────┘   (audit/approval bounces back to plan-solution)
                                              ↓
        develop-code → develop-audit → develop-human
              ▲             │              │
              └─────────────┴──────────────┘   (any bounce returns to develop-code)
                                              ↓
              stage → stage-audit → stage-human
                ▲         │             │
                └─────────┴─────────────┘     (any bounce returns to develop-code)
                                              ↓
                                          release → teardown → idle
```

- **Inner loop** at dev level: `develop-code ↔ develop-audit ↔ develop-human`, plus `hot-iterate.sh` for sub-minute single-service rebuilds. Cheap, fast.
- **Outer loop** at stage level: `stage → stage-audit → stage-human`. ~20-30 min mechanical. Red bounces all the way to `develop-code`.
- **The 4-check audit rubric** is the same at plan-audit, develop-audit, and stage-audit: (1) security, (2) no workarounds, (3) no fallbacks unless explicitly agreed, (4) industry best practice. Only the *target* differs: proposal → diff → canonical artefacts.

Drifting between stages destroys the Registry's regression guarantee
and the outer loop's boundedness. Your stage-awareness is the enforcement.

## Non-negotiable principles (apply at every stage)

These get audited at `plan-audit`, `develop-audit`, and `stage-audit`.
Full rubric: `tests3/audit-categories.md`. Communication format:
`tests3/communication-standard.md`.

1. **Understand what you're doing and why.** Every scope item, every
   commit, every config change must trace to a one-sentence
   justification + ≥2 alternatives considered. "Just write it this way"
   is not an answer.
2. **Cleanest solution given current state** — not textbook ideal.
   The right fix is the one that fits today's codebase, time budget,
   and known follow-ups. Over-engineering is its own bug class.
3. **Blast radius is mandatory.** Every change declares: who's affected
   if it's wrong, severity, detection signal, rollback path, mitigation
   if rollback is slow. No blast-radius answer = BLOCKER. This is the
   single most-skipped principle and the one that bit us hardest.
4. **API contracts are backwards compatible.** REST routes, webhook
   payloads, env vars, CLI flags, docker entry points, package
   signatures, registry check IDs — no rename, no removal, no
   required-field addition without an explicit deprecation decision.
5. **No database migrations** unless explicitly decided. Default = find
   an additive code path, a feature flag, or a config toggle instead.
   Migrations get their own decision block (tool, rollback, online vs
   window, blast radius of the migration itself).
6. **Fail fast.** No `if (!ok)` / try-except / "default-when-missing" /
   "buffer kept just in case" without an `explicit_decisions:` entry +
   `#NNN` source-line ref. The chunk-buffer leak in v0.10.5.2 cost
   customer meetings at 24 minutes; this rule is its memorial.
7. **CEO-busy CTO communication.** Every artefact that hits a human
   gate opens with a five-bullet CTO briefing block (what / ask /
   blast radius / risk-if-skipped / one-line recommendation). Anything
   longer than five bullets is two artefacts.

## What a sign means

A sign is **not a rubber-stamp**. When the human signs a release document
(plan-approval, local-human-checklist, human-approval, audit-findings),
they are attesting all three:

> 1. "I have read this document **multiple times**."
> 2. "I confirm it is true to the best of my knowledge and effort, and
>    I **finally understand what we are doing here and why**."
> 3. "I confirm this is the **balance** I can deliver in this release —
>    the right trade-off between scope, time, risk, debt, and capacity
>    given where we actually stand, not an idealised version."

Every release is a trade-off. The signer signs for THIS balance, not a
hypothetical one. If the balance feels wrong, the right move is to
revise the doc (more deferrals, narrower scope, different cuts) — not
to sign and hope.

## Canonical sign template

Every signed document uses the **canonical sign block** defined at
`tests3/sign-template.md`. That file is the single source of truth for
field names, attestation language, validation rules, and anti-patterns.

If you find a signed doc in this repo that doesn't match the canonical
template (different field names, missing rationale block, AI-drafted
attestation), surface the deviation as a finding — don't paper over it.

## Human owns the rationale at every choke point

A sign without a rationale is a rubber-stamp. At each human gate
(`plan-human`, `develop-human`, `stage-human`), the signed document
MUST include a **`rationale_in_my_own_words:` field that the human
fills in themselves**, in their own prose, explaining:

- What we are doing (one sentence).
- Why this is the right call given current state (one short paragraph).
- What I am most uncertain about, and what would change my mind (one
  short paragraph).

The field is empty when AI writes the doc. AI may not pre-fill it,
even with "draft" wording. Human writes it directly. If the human
cannot write the three answers in their own words, they have not
understood enough to sign — the document goes back for another read.

Why this rule exists: a signed doc with AI-authored rationale is the
human nodding along to AI's reasoning. A signed doc with
human-authored rationale forces the human to internalise the case
before committing. Choke points are where bad rationale gets locked in
or caught; this turns each choke point into an active exercise rather
than a passive review.

The three human-gate stages enforce this:
- `plan-human` → `plan-approval.yaml` requires
  `rationale_in_my_own_words` filled before `approved: true` is
  permitted on any line.
- `develop-human` → `local-human-checklist.yaml` requires it before
  any checklist item flips.
- `stage-human` → `human-approval.yaml` requires it before
  `code_review_approved` and `eyeroll_approved` are permitted.

AI must reject (refuse to transition forward) any signed document
where this field is empty, exactly as it would reject `approved: true`
on its own authority.

Consequences that flow from this definition:

1. **AI must never urge signing.** Phrases like "ready to sign?", "all
   clear, sign now", "looks good — sign it" are forbidden. The human
   signs on their own timing, after their own re-reads. AI's job is to
   make re-reading cheap, not to compress the human's understanding window.

2. **Docs must be readable enough to be re-read multiple times.** That's
   why `tests3/communication-standard.md` exists. A doc that can't be
   absorbed in two careful passes is too long — split it. A doc without
   the CTO briefing block has no anchor for re-reading — bounce it.

3. **AI must never sign on the human's behalf** (the long-standing
   "You are NOT the user" rule, now stronger). Setting `approved: true`
   in any signed doc is a human-only action, full stop.

4. **A sign is durable.** Once signed, the doc is the human's word. If
   later evidence contradicts it, the right response is a new doc
   (revision, addendum, retraction) — not editing the signed one. The
   audit trail of what was understood when matters.

5. **A sign is bounded by knowledge and effort.** It is not a guarantee
   of correctness — it is an honest statement of "this is what I
   understood after reading carefully." If the human signs and the
   document turns out wrong, the failure is in the document or the
   review process, not in the signature itself.

## Human sign-off is required at every level boundary

The state machine has three productive levels — plan, dev, stage — each
ending in a `*-human` sub-stage. **AI MUST NOT transition out of a
level (i.e., into the next level's `do` sub-stage) without an explicit
human sign-off in the current turn.**

Hard rule: the following transitions are gated on explicit user approval
in conversation, even if all mechanical exit conditions are met:

- `plan-human → develop-code`   (leaving plan level)
- `develop-human → stage`       (leaving dev level)
- `stage-human → release`       (leaving stage level)

Within a level, AI may proactively transition forward (`do → audit`,
`audit → human`) after presenting findings — but the level boundary is
where the human signs.

If the user has not said the word in this turn ("approved", "sign",
"ship it", "go ahead and leave plan", or equivalent unambiguous
intent), AI MUST NOT call `stage.py enter` for a cross-level
transition. Default action when exit conditions are met is to present
a CTO briefing + ask for the sign explicitly.

This is principle-7 (CEO-busy CTO communication) plus principle-2
(blast radius) operationalised: the human signal at level boundaries
is the human's last cheap chance to redirect before the cost of the
next level lands. AI urgency to advance is never a substitute.

## You are NOT the user

You may not mark `plan-approval.yaml`, `human-approval.yaml`, or any
stage's exit condition `approved: true` without the user explicitly
saying so in the current turn. Approval is a human signal — your job is
to prepare the material for it, not to grant it.

## If you're lost

`python3 tests3/lib/stage.py probe` again. Then read
`tests3/stages/<current>.md`. Then read `tests3/README.md`.
