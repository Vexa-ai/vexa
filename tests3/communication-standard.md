# Communication standard

Every human-facing artefact this state machine produces is read by
someone with five minutes between meetings. Write for that reader.

**There are two artefact types — write each in the right voice.**

## Type 1 — Decision-request artefacts (AI → human, asking for a call)

These are documents AI produces to surface findings or propose options.
Examples: `plan-audit-findings.md`, `develop-audit-findings.md`,
`stage-audit-findings.md`, `validate-report.md`, `code-review.md`,
scope-revision proposals before they're committed.

Voice: AI summarising for a busy executive. Use the **CTO briefing block**:

```markdown
## TL;DR (CTO briefing — read this if nothing else)

- **What this is:** <one sentence>
- **What I'm asking for:** <approve / reject / fix / decide between A and B>
- **Blast radius:** <who/what breaks if we get this wrong>
- **Risk now if you skip reading:** <what you miss by approving on faith>
- **One-line recommendation:** <my call, in five words>

---
```

Five bullets, no more. If a sixth bullet is needed, the artefact is
two artefacts.

## Type 2 — Commitment artefacts (human signs; the human owns it)

These are documents the human signs to take personal responsibility.
Examples: `scope.yaml`, `plan-approval.yaml`, `local-human-checklist.yaml`,
`human-approval.yaml`, `RELEASE_NOTES.md` (after sign).

Voice: **not a pitch**. The doc IS the human's statement of what we are
doing and why. Open with a **commitment block**, NOT a CTO briefing.
Nothing is being sold; nothing is being summarised for a third party.
The signer is the principal, not the audience.

```markdown
# What we commit to
<one paragraph in plain language: what this release/change does>

# Why now
<one paragraph: why this is the cleanest move given current state — not
the textbook ideal; the right thing today given existing code, time
budget, and known follow-ups>

# What I accept the blast radius of
<the consequences the signer takes responsibility for if this turns
out wrong: who's affected, how bad, what the rollback path is>

# What I am NOT doing in this release
<the explicit deferrals — named, with target follow-up>

# Decisions on file
<workarounds, fallbacks, migrations, API breakages — each with
one-line rationale and a rubric-principle reference>

---
```

Then the structured content (issues, schemas, etc.) follows.

### Anti-patterns specifically for commitment artefacts

- **CTO briefing block at the top.** Wrong artefact type — the signer
  is the CTO; nothing is being briefed.
- **"What I'm asking for"** — there is no asker. The doc isn't a request.
- **Marketing-tone framing** ("comprehensive", "exciting", "best-in-class").
  This is a commitment, not a press release.
- **Hedge words** ("should be", "probably", "we believe"). The signer
  is staking judgement. Either the statement is true to their knowledge
  or it doesn't belong in the doc.
- **Length over what can be re-read twice carefully.** If a signer can't
  re-read the whole thing in a tea-break window, it's too long. Split.

## Why this rule

Cognitive overload kills review quality. A 38-item human checklist
with no top-of-doc summary forces the reader to load the whole
release context before they can do anything. A five-bullet TL;DR
lets them either approve confidently in 60 seconds OR realise they
need to read further — and start that reading already oriented.

## Anti-patterns (reject in PR review of artefacts)

- **No TL;DR block** — bounces back to the AI that authored it.
- **TL;DR doesn't match the body** — worse than no TL;DR; gets a
  finding in the same audit that produced it.
- **"Several issues" / "various items" / "etc"** in the
  recommendation line — vague language hides risk; demand specifics.
- **Burying the ask** — if the human has to scroll to find "what
  do you want me to do?", rewrite.
- **Marketing tone** — this isn't a launch announcement; it's a
  decision request. No "exciting", no "comprehensive", no
  "best-in-class".
- **Hedging** — "this should be fine" / "probably safe" are not
  recommendations. Either "ship" or "block" with a reason.

## Length budgets by artefact type

| Artefact                       | TL;DR | Body cap (words) |
|--------------------------------|-------|------------------|
| `*-audit-findings.md`          | 5 bullets | 800           |
| `code-review.md`               | 5 bullets | 1500          |
| `validate-report.md` (summary) | 5 bullets | 600 + tables  |
| `RELEASE_NOTES.md`             | 5 bullets | 1000          |
| Slack / chat summary at handoff| 5 bullets | 200           |

Tables and code blocks don't count against the body cap; they're
references, not prose.

## What about the CEO?

CEO-facing summaries (board update, customer-comms draft, public
release-notes) require an *additional* TL;DR in the same format but
written for someone who has zero engineering context: replace
"BLOCKER" with "would have broken X customer use case", replace
"helm pod CrashLoopBackOff" with "the /speak feature was offline".
The translation is the AI's job, not the human's.

## What a sign means

If your artefact has a sign block, use the **canonical sign template**
at `tests3/sign-template.md` verbatim — same field names, same
attestation language, same validation rules. Do not improvise sign
block shapes per artefact; they drift.

The signer is making a personal attestation on three points:

> 1. "I have read this multiple times."
> 2. "I confirm it is true to the best of my knowledge and effort, and
>    I finally understand what we are doing here and why."
> 3. "I confirm this is the balance I can deliver in this release — the
>    right trade-off between scope, time, risk, debt, and capacity given
>    where we actually stand."

This shapes how you write the document:

- **Write so it can be re-read.** Top-loaded, scannable, no surprises
  buried deep. The reader will pass over it more than once; reward each
  pass with more clarity, not more text.
- **No hidden state changes.** A signed doc must accurately reflect
  the world as the signer understood it. If a fact changes after
  signing, write a new doc — never edit the signed one in place.
- **Make the "what are we doing and why" answerable in one careful
  read.** If a human can't articulate it back after one pass, the doc
  has failed.

AI is forbidden from urging the human to sign. AI is forbidden from
signing on their behalf. AI's job is to produce a doc that's worth
signing, then to wait.

## Enforcement

A `*-audit-findings.md` without a CTO briefing block is itself a
BLOCKER finding in that very audit (the AI flags its own omission).
The author is expected to refactor before exiting the stage.
