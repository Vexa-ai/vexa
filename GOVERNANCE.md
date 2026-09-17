# Governance

How this repository is run: who decides what, how a pull request reaches `main`, and how you move
from opening your first issue to holding the commit bit.

Vexa is an Apache-2.0 project and a [FINOS](https://www.finos.org/)-shaped one — this file follows
the same practice as [`Vexa-ai/vexa-core`](https://github.com/Vexa-ai/vexa-core), whose
`MAINTAINERS.md` is the canonical leadership record for the project.

Related: [`MAINTAINERS.md`](MAINTAINERS.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md) ·
[`VALIDATION.md`](VALIDATION.md) · [`CONTRIBUTOR_RIGHTS.md`](CONTRIBUTOR_RIGHTS.md) ·
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) · [`SECURITY.md`](SECURITY.md)

---

## The principle

**Contributors write, build, test and review. Maintainers merge and release.**

A maintainer does not read every line of every diff — that does not scale and it is how this
repository ended up with pull requests waiting two months for a first look. A maintainer reads the
**merge card**: what the machine proved, which humans ran the change, what they saw, and the
security-sensitive or architecture-crossing parts of the diff. Everything else is the community's.

---

## Roles

| Role | What you may do | How you get there |
|---|---|---|
| **Contributor** | Open issues and pull requests. | Open one. |
| **Validator** | Everything above. Your `Validated on …` comments count toward the merge rule. | **Post one accepted validation.** No vote, no ceremony — the first one makes you a validator. See [`VALIDATION.md`](VALIDATION.md). |
| **Reviewer** | Your approving review counts as the peer review in the merge rule. | Three accepted validations, and one pull request you authored or validated has merged. Recorded in the weekly meeting minutes. |
| **Triager** | Labels, assignees, milestones, closing and reopening issues, marking duplicates, requesting reviews. GitHub `triage` permission. Not merge, not push. | Six accepted validations or reviews. Proposed at the weekly meeting; carries if no triager or maintainer objects. The maintainer grants the permission. |
| **Maintainer** | Merge, release, repository settings. | Proposed at the weekly meeting and decided by the existing maintainers. Recorded by pull request in [`MAINTAINERS.md`](MAINTAINERS.md). |

**The ladder is measured in validations given, not pull requests authored.** Validations are the
scarce resource here, so that is what counts.

**Inactivity is not misconduct.** A triager or maintainer with no activity for 90 days moves to
*emeritus* in [`MAINTAINERS.md`](MAINTAINERS.md) and the permission comes off. Coming back is one
sentence at a meeting.

---

## How decisions are made

**At the weekly community meeting**, by lazy consensus: a proposal that draws no objection from a
triager or maintainer, during the meeting and for seven days after, carries. Every decision is
recorded in that meeting's minutes ([`meetings/`](meetings/)) so the record is public and dated.

Three things are never decided by lazy consensus: **maintainership** (the existing maintainers
decide), **the licence and contributor-rights policy** (see [`CONTRIBUTOR_RIGHTS.md`](CONTRIBUTOR_RIGHTS.md)),
and **repository security settings**.

---

## The merge rule

A maintainer merges a pull request when **all** of the following hold.

1. **Human validation.** At least **one** non-author human has run the change on their own instance
   and posted the fixed-shape comment from [`VALIDATION.md`](VALIDATION.md). **Two** are required
   when the change touches a **core path**:

   - `core/gateway/**` — authentication and the public edge
   - `core/meetings/services/bot/**` — the bot runtime
   - storage and persistence — `**/migrations/**`, storage adapters, `deploy/**` persistence
   - anything billing-adjacent

2. **Peer review.** At least one approving review from someone who is not the author, on the current
   head commit. A new push dismisses a stale approval.

3. **Every gate green.** `gates`, `pr-value` where it applies, `merge-card`, `contribution-rights`,
   and DCO. **Nothing merges red, and no label waives a check.**

4. **The maintainer's read** — a security and architecture read of the parts the merge card
   surfaces, not a line-by-line read of the diff.

5. **Contribution rights satisfied** — exactly one rights declaration, a DCO `Signed-off-by` on
   every commit, and corporate authorisation verified where it applies
   ([`CONTRIBUTOR_RIGHTS.md`](CONTRIBUTOR_RIGHTS.md)).

The maintainer then merges and applies `state: value-signed`, which is the **record that human
evidence was present** — never a substitute for it.

> **An agent's own tests are not human evidence.** A pull request prepared by an agent enters the
> same queue as everyone else's and merges on the same rungs. This applies to the maintainers' own
> agent-authored pull requests, without exception. A validation comment names the human who ran it.

---

## The queue

Open pull requests are ordered by **declared demand**, then by **age**, oldest first.

- **Declared demand** is public and costs nothing: a 👍 reaction on the pull request, or a comment
  saying you run Vexa and need this, with one line about your deployment.
- **Age** breaks ties. Always oldest first.

A pull request that is 30 days old with no declared demand, no human validation, and no response
from its author is closed with `declined: stale` and a note. **Closing is not rejecting** — the
branch survives, reopening is one click, and a queue that never closes anything is a queue nobody
reads.

---

## The weekly meeting

**Every Wednesday, 14:00 UTC, 45 minutes.** Open to anyone; the link never changes and no invitation
is needed. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the link and the calendar subscription.

The meeting is the triage ritual. We walk open pull requests and issues **oldest first**, and each
one leaves with an owner and a label, or it is closed. Blocked contributors come before the queue.

The call is transcribed by a Vexa bot — the project uses its own product — and the minutes are
published the same day as a GitHub Discussion, with the transcript linked. Template and index:
[`meetings/`](meetings/).

**You do not have to attend.** The board and the minutes carry everything, and no pull request is
disadvantaged by a timezone.

---

## What contributors get back

- **Your pull request gets its human test when you have tested someone else's.** A norm, not a
  check. Your first contribution is never held hostage to it.
- **The ladder above**, measured in validations given.
- **Credit in the release notes** — every release names the validators alongside the authors.

---

## Reporting

Conduct concerns: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Security vulnerabilities:
[`SECURITY.md`](SECURITY.md) — never in a public issue, and never at the weekly meeting.
