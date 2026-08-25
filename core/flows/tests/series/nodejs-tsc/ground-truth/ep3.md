# Episode 3 — 2026-04-01 · what was actually going on

> **DERIVED.** Distilled by us from the organizer's published minutes,
> [nodejs/TSC/meetings/2026-04-01.md](https://github.com/nodejs/TSC/blob/main/meetings/2026-04-01.md).
> Not the minutes. Where the two disagree, the minutes win.
>
> **Fixture is trimmed to the first 40 of 54:54 minutes.** The AI debate runs to the end of the
> meeting, so its final exchanges are recorded here but are **not in the transcript**. A scaffold
> that stops earlier than this file does is not necessarily wrong.

**The episode the series was pointing at.** Attendance triples: **nineteen people**, including
four guests who appear nowhere else — Jacob Smith, Fedor Indutny (TSC emeritus), Joe Sepi (CPC
rep), Maël Nison — plus Robin Ginn speaking for OpenJS. The meeting is almost entirely one item.
The date was set for this in episode 1, three weeks earlier.

**The item:** vote on AI contributions ([nodejs/TSC#1831](https://github.com/nodejs/TSC/issues/1831),
[nodejs/node#62105](https://github.com/nodejs/node/pull/62105)). Context announced up front: the
OpenJS **AI-assisted development policy was approved** by the Board — the thing episode 1 said was
going to the Board on the 27th. So the TSC is now debating whether to take a stricter line than a
policy that already binds it by inheritance.

**The argument, by position:**
- **Fedor Indutny** (guest, emeritus, works at Signal, speaking personally) argues the TSC should
  **reject AI use outright**. His case is about attribution: open source's currency is credit,
  and AI is built to strip it; reviewing AI code is an *audit*, not a review, because the output
  is designed to look plausible; "you are responsible for the code you write" shifts the problem
  to the contributor instead of addressing it. He has a petition with a few hundred signatures.
  He argues corporate AI mandates suppress dissent, and that inaction means the OpenJS policy —
  which *encourages* AI — becomes Node.js's position by default.
- **Matteo Collina** points at the TSC charter's stated responsibilities and a Linux Kernel
  summary; argues AI assistance is why contributor numbers are back to 2016 levels, that a global
  ban makes a first-time contributor's first interaction a rejection, and that a ban incentivises
  lying.
- **Robin Ginn (OpenJS)** reports the policy was reviewed by LF and OpenJS counsel, is in the
  spirit of the Linux Kernel policy, matches what Kubernetes, React and PyTorch adopted, and
  passed the Board **unanimously**.
- **James Snell** repeatedly presses the procedural question nobody answers: *why are the
  existing review policies not sufficient?* Notes everyone agrees disclosure is right, and asks
  whether a valid bugfix would be rejected for its authorship.
- **Antoine du Hamel** probes enforceability — can this be enforced, and would it push people to
  lie or stop contributing? Fedor: enforceability does not matter, the stance does.
- **Jacob Smith** supports the review-burden point: plausible-looking output needs extra scrutiny.
- **Ruy Adorno** raises commentary around the Claude Code source leak and concealment of AI use.

Fedor's closing analogy compares the terminology to the removal of "master/slave" from core:
technically valid is not the same as right for the community. **No decision is recorded** — the
minutes trail off; the vote follows the discussion.

**Also announced:** Node.js Interactive colocated with RenderATL (speakers rolling out);
in-person Collab Summit registration deadline 3 April; DCO/sign-off trailer for commits landing
on `nodejs/node` ([core-validate-commit#141](https://github.com/nodejs/core-validate-commit/pull/141),
[nodejs/node#62510](https://github.com/nodejs/node/pull/62510)).

**What the series makes visible and one meeting cannot:** this debate was scheduled in episode 1,
skipped in episode 2 on purpose, and the Board approval it reacts to was foreshadowed in episode
1. The guest list is the tell — a scaffold that treats these nineteen as the standing roster has
learned the wrong thing about who this meeting belongs to.

## Entities
- Node.js Technical Steering Committee
- AI contributions vote
- Fedor Indutny
- Matteo Collina
- James Snell
- Antoine du Hamel
- Robin Ginn
- Jacob Smith
- Ruy Adorno
- OpenJS Foundation
- AI-assisted development policy
- Linux Kernel
- attribution
- Collab Summit
- Node.js Interactive
- RenderATL
- DCO
- Claude Code
- contributor spotlight
