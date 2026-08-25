# Episode 1 — 2026-03-18 · what was actually going on

> **DERIVED.** Distilled by us from the organizer's published minutes,
> [nodejs/TSC/meetings/2026-03-18.md](https://github.com/nodejs/TSC/blob/main/meetings/2026-03-18.md).
> Not the minutes. Where the two disagree, the minutes win.

A thin-attendance, heavy-agenda TSC meeting. Six voting members present. The chair drives fast
through announcements and a long list of `tsc-agenda`-labelled issues.

**The shape of the meeting** — a fixed recurring skeleton the scaffold should learn on episode 1
and expect on every later one: Announcements → Reminders → CPC and Board Meeting Updates →
per-repo agenda sections (`nodejs/TSC`, `nodejs/admin`, `nodejs/node`) → Strategic Initiatives →
Upcoming Meetings. Agenda items are GitHub issues and PRs, pulled in automatically by label.

**Announced:** Node.js Interactive is running as part of RenderATL (call for speakers open);
Collab Summit is going ahead and sessions are open for submission; a security release is coming
next week across all release lines.

**The thread that starts here.** The vote on AI contributions
([nodejs/TSC#1831](https://github.com/nodejs/TSC/issues/1831)) is scheduled: Matteo sets the
discussion for **1 April** to suit US timezones, with the vote to start afterwards, and notes the
AI-assisted engineering policy is going to the OpenJS Board on the 27th. Filip flags misleading
titles circulating about it. Matteo takes on comms. *A scaffold reading only this episode sees an
announcement; the series shows it is the spine of episode 3.*

**The longest live discussion — ncrypto.** On `tools: add ncrypto updater script`
([nodejs/node#61613](https://github.com/nodejs/node/pull/61613)) the room does not converge:
Filip and Antoine debate which direction the sync should run (node→ncrypto vs ncrypto→node,
Antoine arguing the latter is much simpler); Marco objects that moving ncrypto out first makes
security releases harder and prefers keeping it in core to avoid coordination; Matteo defers it,
asking for the people who actually benefit to be in the call, and proposes James host a session
on ncrypto at the Collab Summit. **Unresolved, parked on a future event** — the kind of item that
is invisible unless the note records that nothing was decided.

**Rust ownership.** Creating a rust team ([nodejs/admin#1047](https://github.com/nodejs/admin/issues/1047))
has had no objections for two weeks. On ownership of Rust crates
([nodejs/admin#1045](https://github.com/nodejs/admin/issues/1045)) Antoine argues Node.js has no
plan to consume from Cargo, so a Rust pipeline only adds maintainership burden. Also runs in
episodes 2 and 3.

**Closed:** transfer of `DataDog/dc-polyfill` to the org
([nodejs/admin#1019](https://github.com/nodejs/admin/issues/1019)) — already approved, dropped
from the agenda.

**Also raised, without discussion:** charter update on communication responsibilities
([#1754](https://github.com/nodejs/TSC/pull/1754)), self-serve funding model
([#1747](https://github.com/nodejs/TSC/issues/1747)), draft SoW for a test-reliability lead
([#1629](https://github.com/nodejs/TSC/issues/1629)), AI guidelines doc
([nodejs/node#62105](https://github.com/nodejs/node/pull/62105)), Virtual File System
([nodejs/node#61478](https://github.com/nodejs/node/pull/61478)).

## Entities
- Node.js Technical Steering Committee — the meeting's own body; "TSC" throughout
- ncrypto — the crypto library being extracted from core; episode 1's longest argument
- Antoine du Hamel
- Matteo Collina
- Filip Skokan
- Marco Ippolito
- Michaël Zasso
- Robert Nagy
- Collab Summit
- Node.js Interactive
- RenderATL
- OpenJS Foundation
- CPC — Cross Project Council, a standing agenda section
- AI contributions vote
- Rust crates
- contributor spotlight
- security release
