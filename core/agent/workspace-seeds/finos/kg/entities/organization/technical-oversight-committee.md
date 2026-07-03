---
type: organization
id: technical-oversight-committee
title: Technical Oversight Committee
---

The Technical Oversight Committee (TOC) is [[FINOS]]'s technical governing body, working
with the FINOS team and Governing Board to provide technical oversight for projects in
the FINOS portfolio. Roles: Member, Vice Chair, Chair.

Repo: https://github.com/finos/technical-oversight-committee
Meets: private Tuesday TOC-planning sessions; public Wednesday sessions for project/SIG
updates. Reachable via toc@lists.finos.org.

## Current members (as of 2026-06-30, term ends Feb 25, 2027 unless noted)

| Name | Role | Company |
|------|------|---------|
| [[Peter Smulovics]] | Chair | [[Morgan Stanley]] |
| [[Maria McParland]] | Vice Chair | [[JPMC]] |
| [[Gabor Liptak]] | Member | [[Capital One]] |
| [[Chris Twiner]] | Member | [[UBS]] |
| [[Eddie Knight]] | Member | [[Sonatype]] |
| [[Matthew Bain]] | Member | [[Morgan Stanley]] |
| [[Vincent Caldeira]] | Member | [[Red Hat]] |
| [[Elspeth Minty]] | Member | [[Royal Bank of Canada]] |
| [[John Arroyo]] | Member | [[Citi]] |
| [[Flamur Gogolli]] | Member | [[AWS]] |
| [[Andrew Aitken]] | Member | Individual Contributor |
| [[Ganesh Harke]] | Member | [[Citi]] |

Source: https://github.com/finos/technical-oversight-committee/blob/main/Readme.md

## Invocation log

Structured run-by-run record of every `finos-discovery` routine invocation. One row
per run, appended in order; pairs with the detailed notes in the Discovery log below.

| # | Date | Trigger | Entities added/updated | Summary |
|---|------|---------|-------------------------|---------|
| 1 | 2026-06-30 | scheduled | 3 | Seeded TOC roster; added Trevor O'Brien, Sergio Gago, Maurizio Pillitu. |
| 2 | 2026-06-30 | scheduled | 9 | Walked all 15 TOC Candidacy issues; added 9 candidates. |
| 3 | 2026-06-30 | scheduled | 1 | Checked finos/community issues (no new info); added Jamie Slome. |
| 4 | 2026-06-30 | scheduled | 2 | Paged finos org members p2; added Gabriele Columbro, Rob Moffat. |
| 5 | 2026-06-30 | scheduled | 5 | Finished paging finos org members; added 4 people + Perspective project. |
| 6 | 2026-06-30 | scheduled | 0 | Swept remaining TOC repo issues — no new entities found. |
| 7 | 2026-06-30 | user-directed | 26 | Pulled full finos org repo list (191 repos); scaffolded 26 project entities. |
| 8 | 2026-06-30 | scheduled | 5 | Pulled git-proxy/legend-engine/FDC3 contributor lists; added 3 people + 2 companies. |
| 9 | 2026-06-30 | scheduled | 4 | Checked new TOC repo issues + remaining git-proxy contributors; added 3 people + 1 company. |

## Discovery log

- 2026-06-30: Seeded roster from Readme.md. Discovered candidacy issue #147 for
  [[Trevor O'Brien]] ([[Moody's]]), supported by [[Sergio Gago]] ([[Moody's]]). Issue
  #105 (onboarding checklist) names FINOS staffer maoo ([[Maurizio Pillitu]]).
  Next: walk open/closed candidacy issues for more candidates, and PR/issue authors
  across finos/technical-oversight-committee and finos/community for more people+companies.
- 2026-06-30 (pass 2): Listed all `[TOC Candidacy]` issues in the repo (15 total).
  Wrote up 9 more candidates: [[Valentina Rodriguez Sosa]] ([[Red Hat]], #290, open),
  [[Sai Sravan Cherukuri]] ([[IRS]], #287, open), [[Jey Paulraj]] ([[Red Hat]], #285,
  open), [[Boris Litvin]] ([[AWS]], #281, open), [[Maranda Harris]]
  ([[CompliLedger]], #280, open), [[Erick Bourgeois]] (RBC Capital Markets /
  [[Royal Bank of Canada]], #278, open), [[Suren Konathala]] ([[Capgemini]], #235,
  closed — outcome unstated), [[Jon Freedman]] (independent, #234, closed — outcome
  unstated), [[Tim Paine]] ([[Point72]], #95, closed — outcome unstated). Note: some
  candidacy issues (#289 Eddie Knight, #282 Matthew Bain, #241 Gábor Lipták, #236
  Peter Smulovics) are term-renewals for people already in the roster above — not
  written up separately.
  Next: open candidacies (#290, #287, #285, #281, #280, #278) have no stated outcome
  yet — recheck later for approval/rejection. Still unexplored: PR/commit authors in
  finos/technical-oversight-committee, finos/community issues, and the
  github.com/orgs/finos/people roster for maintainers not yet captured here.
- 2026-06-30 (pass 3): finos/community open issues (#418 broken links, #417 Jupyter
  FS proposal, #358 project landscape update) had no new people/companies — skipped.
  Checked github.com/orgs/finos/people: 30+ members on page 1 alone (paginated),
  most without visible employer info. Many GitHub handles encode employer as a
  suffix (e.g. `-ms` = Morgan Stanley, `-redhat`, `-moodys`) — useful signal for
  future passes, but didn't bulk-import unconfirmed members this run. Confirmed and
  added one: [[Jamie Slome]] ([[Citi]] OSPO Operations Lead, GitProxy maintainer).
  Brian Warner searched, no confirmed FINOS connection found — not added (no invented
  facts).
  Next: page through github.com/orgs/finos/people further (pages 2+) and cross-check
  `-ms`/`-redhat`/`-moodys`-suffixed handles against company; check finos/git-proxy,
  finos/legend-engine, or other flagship repos' top contributors for more
  maintainer↔company pairs; recheck #290/#287/#285/#281/#280/#278 candidacy outcomes.
- 2026-06-30 (pass 4): Rechecked #290 (Valentina Rodriguez Sosa) — still no
  comments/labels indicating a vote outcome; candidacy issues appear to not record
  decisions in-thread, so further outcome-checking on #287/#285/#281/#280/#278 is
  likely low-yield — deprioritizing that thread. Paged github.com/orgs/finos/people
  page 2 (29 more handles); most already-known names matched (maoo, rocketstack-matt,
  timkpaine). Added two FINOS staff: [[Gabriele Columbro]] (founding Executive
  Director, @mindthegab) and [[Rob Moffat]] (Senior Technical Architect / FDC3 lead,
  @robmoffat) — linked both into [[FINOS]]'s Staff list. Other page-2 names
  (Patrick Mylund Nielsen, Nuritzi Sanchez, Andrew Stein, etc.) not yet
  company-confirmed — skipped rather than guess.
  Next: page 3+ of github.com/orgs/finos/people; resolve company for Patrick Mylund
  Nielsen, Nuritzi Sanchez, Andrew Stein (texodus), Stephen Goldbaum if findable;
  check finos/git-proxy and finos/legend-engine top contributors for maintainer↔company
  pairs.
- 2026-06-30 (pass 5): Finished paging github.com/orgs/finos/people — page 3 was the
  last (org has ~70 members total across 3 pages). Resolved and added: [[Patrick
  Mylund Nielsen]] ([[Clovyr]] CTO, ex-JPMC Quorum lead), [[Nuritzi Sanchez]]
  ([[GitLab]] Sr. OSPM, ex-GNOME Foundation president — FINOS-specific role
  unconfirmed), [[Andrew Stein]] ([[Prospective Co.]] founder/CTO, FINOS Perspective
  lead maintainer, ex-JPMC), and FINOS staffer [[Tosha Ellison]] (Strategic Advisor,
  ex-COO). Created a [[Perspective]] project entity linking Andrew Stein (lead
  maintainer) and Tim Paine (core maintainer). Stephen Goldbaum not researched yet.
  Next: Stephen Goldbaum and remaining page-1/2 names without confirmed company
  (Adwoa-Konadu-Appiah, Aitana Myohl, Brian Ingenito, etc.) — only pursue if they
  show up again via issues/PRs (don't cold-search the whole roster, diminishing
  returns). Switch focus: check finos/git-proxy and finos/legend-engine top
  contributors, and finos/community open PRs, for maintainer↔company pairs not yet
  captured.
- 2026-06-30 (pass 6): Sorted finos/technical-oversight-committee issues
  newest-first to check for anything missed. #291 and #284 (June 17 / June 3 2026
  meeting minutes) are both blank templates — no attendees, decisions, or candidacy
  votes recorded yet. #286 is a closed duplicate of #287 (Sai Sravan Cherukuri) — no
  new info. #274 (remove liaison guide from docs, opened by Maria McParland,
  resolved via PR #275) — no new people/companies. This repo's issue history is now
  effectively fully mined — no new entities this pass.
  Next: stop mining finos/technical-oversight-committee issues directly (low
  remaining yield); pivot to finos/git-proxy and finos/legend-engine contributor
  lists, and watch this repo for *new* issues/PRs filed after 2026-06-30 (e.g. future
  meeting minutes getting filled in with attendees, or new candidacies) on subsequent
  runs.
- 2026-06-30 (pass 7, user-directed): Pulled the full finos GitHub org repo list via
  the public API (191 repos total). Created 26 project/initiative entities under
  kg/entities/organization/ covering the major ones (Legend, FDC3, Common Domain
  Model, GitProxy, Architecture as Code, Waltz, Morphir, OpenMAMA, Symphony Platform
  tooling, TimeBase-CE, TraderX, AI Governance Framework, Common Cloud Controls,
  DevOps Automation, a11y Theme Builder, Fluxnova, OpenGRIS, Vuu, kdb+ Working Group,
  TRAC DAP, Open Source Readiness Initiative, Financial Objects Program, Open
  RegTech SIG, InnerSource SIG, Zenith, Fin-OCR) and linked them from [[FINOS]]'s
  Projects list. Skipped ~165 long-tail repos (juju-charm operators, per-language
  Morphir/Fluxnova sub-packages, meta/branding/blueprint repos) as not distinct
  projects — noted in finos.md instead of scaffolded individually.
  Next: contributor lists for individual flagship repos (git-proxy, legend-engine,
  FDC3) for maintainer↔company pairs not yet captured; revisit if new FINOS repos
  appear in the org.
- 2026-06-30 (pass 8): Pulled contributor lists for finos/git-proxy, finos/legend-engine,
  and finos/FDC3. Resolved three new people via GitHub profile lookups: [[Kris West]]
  ([[NatWest Group]], lead maintainer of [[FDC3]], top contributor on both FDC3 and
  git-proxy), [[Brian Ingenito]] ([[Morgan Stanley]], FDC3 contributor — this confirms
  the "Brian Ingenito" name flagged unconfirmed in pass 5's org-roster sweep), and
  [[Rafael Bey-Hernandez]] ([[Goldman Sachs]] TechFellow, top legend-engine contributor).
  Created company entities for NatWest Group and Goldman Sachs. Linked all three into
  the FDC3/Legend project pages and their employer company pages. Noted the `-gs`
  contributor-handle pattern on legend-engine (Legend's GS origin) without bulk-importing
  unconfirmed handles. git-proxy's top contributor JamieSlome and #9 maoo were already
  known; eddie-knight (#24, already a TOC member) confirmed as a git-proxy contributor
  too. Did not pursue Pierre De Belen (#2 on legend-engine, 406 contributions) — no
  company field on GitHub profile, so left unconfirmed rather than guessing.
  Next: resolve Pierre De Belen's company if it surfaces elsewhere (issue/PR comments,
  LinkedIn-style bio); check finos/git-proxy's #2 contributor jescalada and #5
  fabiovincenzi for company; watch finos/technical-oversight-committee for new
  issues/PRs filed after 2026-06-30.
- 2026-06-30 (pass 9): Checked finos/technical-oversight-committee for new issues —
  three filed since pass 6: #294 (update voting process to use LF Project Control
  Center voting + Meek STV, opened by TheJuanAndOnly99), #293 (shift project health
  check to PR-based report instead of mandatory presentation) and #292 (CALM 2026 H1
  semi-annual project report), both opened by rocketstack-matt — already known as
  [[Matthew Bain]], no new entity needed. Resolved #294's author: new person
  [[Juan Estrella]] (GitHub @TheJuanAndOnly99, FINOS staff per bio, Barcelona) — added
  to FINOS staff list. Also resolved the two remaining git-proxy contributors flagged
  last pass: [[Juan Escalada]] and [[Fabio Vincenzi]], both OSS Engineers at
  [[G-Research]] (new company entity) — linked into [[GitProxy]]. Pierre De Belen still
  unresolved (no company on GitHub profile, no further mentions found) — deprioritizing
  further search on him.
  Next: review #292/#293 content once merged for any new project-report process details;
  check finos/legend-engine and finos/git-proxy for *new* PRs/issues opened after
  2026-06-30; consider github.com/finos/community open PRs (not yet checked, only
  issues were checked in pass 3); watch for #290/#287/#285/#281/#280/#278 candidacy
  outcomes resurfacing.
