# Adoption — consolidated report

The self-improvement loop's objective function and everything seven revolutions measured, on a
real product with simulated people. `biz#449`. Line at time of writing: `b0f7fde76`.

> **The number is relative between revolutions. It is never a forecast.** It exists to rank
> changes against each other and is only as good as the rates measured beneath it.

## Objective and definitions (as ruled 2026-09-02)

- **T_full** — simulated **days** until the org reaches full adoption. Reported at 25 / 50 / 80%
  of the *reachable* population, because a single high threshold no arm crosses carries no
  ranking information.
- **Retention** — of those who became active, the share still active at **30** and **90**
  simulated days, plus the **steady state** once the curve flattens.
- **Active** — strict: in the trailing **14 days** the person **opened** a Vexa mail **and** took
  a **UI action** (clicked into the terminal, sent a chat turn, replied, or put the mailbox on a
  meeting they own). Delivered mail scores zero; an open alone is *reached*, not active. An
  **opt-out reply is not an action** — it is leaving.
- **Two layers, stated** — rates are *measured* per (persona × touch × history) on the real
  stack; the org walk *extrapolates* them. Nothing in `personas.py` asserts a propensity.

## Revolution ledger

| r | what changed | bottleneck found | cause (file:line) | fix | before → after |
|---|---|---|---|---|---|
| 1 | first end-to-end run | **the attendee never hears from the product** | `production.py` `email_minutes` → `refs["organizer"]` only | `email_attendees` step, one agent turn, domain allow-list | attendees **0 → 1 mail each**; outside-domain still 0 |
| 1 | — | flow version raced its own admission | `flows/loop.py:64` | refresh-on-miss | permanent failure → resolves |
| 1 | — | workspace leaked into mail (YAML, relative url) | `production.py` `email_minutes` | `_readable()` | frontmatter gone, links absolute |
| 2 | cohorts + synthetic dailies | personas judged meetings they never attended | the harness, not the product | `cohorts.py`, generated dailies | H1 visible for the first time |
| 2 | provenance lines | engineers asked where data lives; coordinators asked why they got it | mail had neither | two lines above the note | open **61.9 → 84.1%**; opt-out **6.3 → 27.0%** |
| 3 | attendee visibility | **attendee's button opened a chat that could not see the meeting** | meeting belongs to organizer; link carried no capability | per-attendee **restricted** `tshare` on the link | 0 → 1 meeting visible; chat *tells* instead of pitching |
| 4 | second-invite offer | **never offered** — mail, chat or chip | `minutes-review` preset ended at one question | `minutes-review-invite` + act-on-yes | OFFERED **0/4 → 4/4** |
| 4 | — | forwarded **Teams/Jitsi** invite silently ignored | `mailbox._meeting_url` (Meet+Zoom only) | all four platforms | forward → `invite_intake` in **10 s** |
| 5 | seed by structure | **89–100% never touched** | pilot design, not product | seed as a parameter | **T80 12–13 d** at every size |
| 5 | offer placement | `unclear_how` from people who *had* the instruction | it sat below the note | moved above it | coordinators **33.3 → 83.3%** |
| 6 | admin bulk seed | admin could not hand the product a **list** | no plural verb anywhere | `POST /events/batch` | 20 meetings, **one call, 0.06 s**, idempotent |
| 6 | — | operator verbs had no authority check | `me()` only | instance-admin gate | refused by name |
| 7 | key + audit | gate stood in front of a door keyed **`changeme`** | `flows_api.py` default; var never exported | no default, per-lane key, `admitted_by` | `changeme → 401` |

## Seed × size — the result that matters

Everything else fixed; only day-0 membership changes. Production cohort.

| seed strategy | seed % | T25 | T50 | T80 | steady | ret90 |
|---|---|---|---|---|---|---|
| 3–5 random organizers *(the current pilot)* | 0.17% | >120 | >120 | >120 | 1.3% | 0.93 |
| one dailies coordinator per show | 0.57% | >120 | >120 | >120 | 13.6% | 0.99 |
| **all coordinators + PMs** | 9.6% | **2** | **5** | **12–13 d** | **80%** | 0.95 |
| admin → mailbox on every dailies | 3.3% | 3 | 6 | >120 | 78% | 0.99 |

**Size-invariant**: identical at 2,000 / 20,000 / 200,000. The current pilot fails at every size
for an arithmetic reason — three people is a constant against a graph that grows 100×.

**Best case** (best seed + personal follow-up + presence): **T80 = 4–7 days, retention 0.96–0.999**
at every size. Presence turns the admin route from "never reaches 80%" into **6–7 days on a 3.3%
seed** — the cheapest complete answer.

## Execution runbook

| | seed (iv) admin → every dailies | seed (iii) onboard every production office |
|---|---|---|
| cost | **one call, one list, 0.06 s for 20**, row per meeting, idempotent | **2 human email replies per person** |
| bounded by | nothing | **the humans** — 200 people ≈ 400 replies |

1. **Prefer (iv).** One paste, admin-gated, idempotent, no per-person onboarding.
2. **Make day one carry the evidence.** The bot's name pointing at the notes is nearly worthless
   alone (0 → 2.1%); the name **plus** the day-1 minutes mail moves coordinators **0 → 41.7%**.
3. **(iii) is a people-plan, not a product plan.** Budget the 400 replies, or cut the two first.

## Hypotheses

| | verdict | evidence |
|---|---|---|
| **H0** the atom is a calendar invite, not a signup | **held** | an attendee with no account clicks, redeems, and is in a chat about their meeting; the account is created *by* the click |
| **H1** the artifact loop dominates | **held** | 5–10× the null on peak active in both cohorts; without it adoption travels only through organizers |
| **H2** presence is free but weak | **half refuted** | multiplies strongly (+23pp on mail) — but *nearly replaces* mail for coordinators (83.3% presence-only). Its solo blocker is `trust_quality` |
| **H3** value is the retention coefficient | **held** | retention tracks touch quality; the personal variant holds (ret90 0.82) where shared bleeds (0.31) |
| **H4** control is the SPI gate | **unmeasured** | sharing default ON shipped; opt-out/deletion visibility never put in front of a persona |
| **H5** groups compound, and matter with size | **unmeasured** | group leg still unproven; no arm tested it |
| **H6** approval pull | **unmeasured** | out of scope, as planned |
| **H7** friction halves | **held** | every removed step moved a stage: the capability (0 → visible), the placement (33 → 83%), the platform parse (0 → 10 s) |

## Open — founder decisions, not mine

1. **The From address vs the watched mailbox are two addresses.** The agent named both in one
   conversation; a person replying to the sender hits an address nothing reads.
2. **The wording of the invite offer** — a marked placeholder everywhere it appears
   (`minutes-review-invite`, `_mailbox_line()`). The *mechanics* were measured on the placeholder
   deliberately; the prose is untouched and staged.

## What v0 does not model

Calendar reality (holidays, timezones, skipped meetings); the terminal UI beyond the click; IT
provisioning and the tenant admitting the bot; anyone telling a colleague out loud; transcription
quality. Quality enters only through what the mails actually say. The production cohort's dailies
are **generated** (`synthetic: true`) — no recorded dailies exists, so those numbers are about
dailies-*shaped* input, not about SPI. Sample sizes are small (n=48 per arm at best) and
run-to-run variance was measured at **±10pp**.

## Calibration plan

The personas are unvalidated priors until the human alpha ledger contradicts them. The ledger
records `acted / hesitated / ignored` with a reason and a duration — deliberately the **same
vocabulary** as `personas.SCHEMA`, so a real row replaces a simulated one with no translation.
On each alpha session: replace the simulated decisions for that touch type with the human ones,
re-derive the rates, and re-run. **A human row always overrides a persona propensity it
contradicts.** Three instrument corrections already came from distrusting our own numbers
(38% silent judge failures; absorbing zeros; an opt-out counted as adoption) — the ledger is the
fourth and the only one that can correct the personas themselves.
