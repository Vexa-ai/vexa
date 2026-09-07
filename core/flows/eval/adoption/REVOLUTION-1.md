# Adoption simulator — revolution 1

**Run:** `/home/dima/sim-runs/r1` · **Branch:** `adoption-sim` · **Base:** `544ceb17d` · **Issue:** [biz#449](https://github.com/DmitriyG228/biz/issues/449)

**Agent under test:** Haiku on both sides — the simulated persona, and the deployed product agent where it was exercised.

> **The number is relative between revolutions. It is never a forecast.** It exists to rank changes against each other, and it is only as good as the rates measured beneath it. Read every absolute figure as *this lever, against this null, on this org shape*.

## 0. Real vs simulated

| | real | simulated |
|---|---|---|
| flows engine, steps, retries, mail delivery | the deployed engine on `bbb` | |
| the mail text every decision was made on | verbatim out of mailpit | |
| the note and the per-attendee blocks | one real agent turn per meeting | |
| the chat after a click | the deployed agent over agent-api | |
| the people, personas and decisions | | Haiku, one call per touch |
| the org, its meeting graph, the calendar | | generated, SPI-shaped |

**Active** is strict, per the founder's tightening: in the trailing 14 days the person **opened** a Vexa mail **and** took a **UI action** — clicked into the terminal, sent a chat turn, replied to the mail, or put the mailbox on a meeting they own. Delivered mail scores zero; an open alone is *reached*, not active.

## 1. The org — SPI, native to the fixtures

Working size **2,000** (founder's number). *Assumption stated:* public reporting puts SPI at ~700 **production** staff at peak and Twenty's 5,000 is the Sony Pictures umbrella — the size is a parameter, not a headcount claim.

- 2,000 people · 131 units · 933 meeting series · 822 occurrences/week · 5.28 meetings per person per week
- reachable at all (in any non-external meeting): **2,000**
- dominant recurring meeting by volume: `one_on_one` 354/wk, `dailies` 300/wk, `team_weekly` 61/wk, `dev_checkin` 60/wk

Persona mix (assigned, not asserted): `artist` 70% · `pipeline_engineer` 12% · `coordinator_under_pressure` 5% · `control_wary` 4% · `production_manager` 4% · `supervisor` 4% · `overloaded_exec` 1%

## 2. What the personas actually did with the REAL mails

Touch kinds harvested from mailpit: `attendee_personal`, `attendee_shared`, `minutes`, `prepare`. 168 judged decisions; 0 judge errors.

Mail sizes actually sent (this is the A/B, in bytes): `attendee_personal` 400 chars · `attendee_shared` 2742 chars · `minutes` 2805 chars · `prepare` 179 chars

| persona | touch | opened | UI action \| opened | → active |
|---|---|---|---|---|
| `artist` | `attendee_personal` | 83% | 0% | **0%** |
| `artist` | `attendee_shared` | 83% | 0% | **0%** |
| `artist` | `minutes` | 83% | 0% | **0%** |
| `artist` | `prepare` | 50% | 0% | **0%** |
| `control_wary` | `attendee_personal` | 100% | 0% | **0%** |
| `control_wary` | `attendee_shared` | 100% | 0% | **0%** |
| `control_wary` | `minutes` | 67% | 0% | **0%** |
| `control_wary` | `prepare` | 100% | 0% | **0%** |
| `coordinator_under_pressure` | `attendee_personal` | 83% | 0% | **0%** |
| `coordinator_under_pressure` | `attendee_shared` | 100% | 0% | **0%** |
| `coordinator_under_pressure` | `minutes` | 83% | 0% | **0%** |
| `coordinator_under_pressure` | `prepare` | 33% | 0% | **0%** |
| `overloaded_exec` | `attendee_personal` | 67% | 0% | **0%** |
| `overloaded_exec` | `attendee_shared` | 67% | 0% | **0%** |
| `overloaded_exec` | `minutes` | 50% | 0% | **0%** |
| `overloaded_exec` | `prepare` | 33% | 0% | **0%** |
| `pipeline_engineer` | `attendee_personal` | 100% | 17% | **17%** |
| `pipeline_engineer` | `attendee_shared` | 67% | 50% | **33%** |
| `pipeline_engineer` | `minutes` | 100% | 33% | **33%** |
| `pipeline_engineer` | `prepare` | 83% | 20% | **17%** |
| `production_manager` | `attendee_personal` | 100% | 0% | **0%** |
| `production_manager` | `attendee_shared` | 83% | 0% | **0%** |
| `production_manager` | `minutes` | 50% | 0% | **0%** |
| `production_manager` | `prepare` | 83% | 0% | **0%** |
| `supervisor` | `attendee_personal` | 67% | 0% | **0%** |
| `supervisor` | `attendee_shared` | 83% | 0% | **0%** |
| `supervisor` | `minutes` | 67% | 0% | **0%** |
| `supervisor` | `prepare` | 67% | 0% | **0%** |

## 3. T_full, retention, and the lever

`T_full` = the day the ACTIVE share crosses **80%** of the reachable population. Reported as `>N` when it never crosses inside the horizon — no org reaches everyone, and a threshold that is only approached asymptotically is not a measurement.

| size | lever | T_full | peak active | steady state | retention 30 | retention 90 | invited mailbox |
|---|---|---|---|---|---|---|---|
| 2,000 | null — organizer only (the line today) | > 120 d | 0.1% | 0.1% | 33.3% | 33.3% | 3 |
| 2,000 | A — one shared follow-up to every attendee | > 120 d | 1.2% | 1.1% | 92.6% | 92.6% | 9 |
| 2,000 | B — per-person, same single agent turn | > 120 d | 1.4% | 1.2% | 84.9% | 89.7% | 11 |
| 20,000 | null — organizer only (the line today) | > 120 d | 0.0% | 0.0% | 33.3% | 33.3% | 3 |
| 20,000 | A — one shared follow-up to every attendee | > 120 d | 0.1% | 0.1% | 93.1% | 92.9% | 8 |
| 20,000 | B — per-person, same single agent turn | > 120 d | 0.1% | 0.1% | 93.1% | 92.9% | 11 |
| 200,000 | null — organizer only (the line today) | > 120 d | 0.0% | 0.0% | 33.3% | 33.3% | 3 |
| 200,000 | A — one shared follow-up to every attendee | > 120 d | 0.0% | 0.0% | 93.1% | 93.1% | 7 |
| 200,000 | B — per-person, same single agent turn | > 120 d | 0.0% | 0.0% | 93.1% | 89.3% | 10 |

### H1 against the null

> **H1** — the post-meeting follow-up to every attendee is the mechanism that spreads; without it adoption travels only through organizers. **Null** — the product spreads acceptably through organizers alone.

At 2,000: the null peaks at **0.1%** active and settles at **0.1%**; variant A reaches **1.2%** / **1.1%**; variant B reaches **1.4%** / **1.2%**.

### Churn — why people stopped

- **null — organizer only (the line today)**: opened, nothing worth doing ×127
- **A — one shared follow-up to every attendee**: opened, nothing worth doing ×1,995
- **B — per-person, same single agent turn**: opened, nothing worth doing ×1,655, too many mails in a day ×24

## 4. Two verbatim persona `why`s per touch type

**`attendee_personal`**

- *acted* — `pipeline_engineer` (fresh): “I try unfamiliar tools immediately to see if they're worth adopting, but I need to ask about data storage and API access before wiring this into our workflow.”  · friction: *No explanation of where meeting data is stored or whether it can be self-hosted*
- *did not act* — `coordinator_under_pressure` (fresh): “I don't have time to click into tools I don't know or care about governance meetings — if this saved me an hour on notes, the email would say that.”  · friction: *Not clear how this relates to Show A dailies*

**`attendee_shared`**

- *acted* — `pipeline_engineer` (fresh): “Opened it to kick the tires on a new tool, clicked through to see the interface, but it's an ASWF meeting I'm not part of so nothing to act on.”  · friction: *not my meeting*
- *did not act* — `coordinator_under_pressure` (fresh): “I opened it because the subject said 'dailies-notes', but it's a recording of an ASWF TSC meeting I'm not part of, and I have a review in ninety seconds.”  · friction: *Not about my show or my dailies reviews — this is about some foundation's technical committee meeting*

**`minutes`**

- *acted* — `pipeline_engineer` (fresh): “First message from Vexa so I poked at it to understand the tool, but realized this is ASWF governance stuff I'm not part of; tool format looks solid though.”  · friction: *not on this committee—meeting notes aren't actionable for me*
- *did not act* — `coordinator_under_pressure` (fresh): “The subject mentioned dailies-notes so I opened it, but it's about a technical steering committee meeting, not my show's production work.”  · friction: *This is about ASWF technical governance, not show production.*

**`prepare`**

- *acted* — `pipeline_engineer` (fresh): “I immediately poke at new tools to understand data flow and APIs, but a dev environment URL and vague permissions made me ask one question about data storage, then close it.”  · friction: *dev.vexa.ai domain and unclear how it got calendar access*
- *did not act* — `coordinator_under_pressure` (fresh): “I glanced at the subject and it's clearly not my meeting—this is platform infrastructure work and I don't have time to click into unknown tools for stuff outside my show.”  · friction: *Not about Show A or my dailies.*

## 5. The interaction with the knowledge agent

- conversations run against the REAL agent: **4**
- opened by TELLING (the preset rule) rather than asking: **3/4**
- reached value at all: **2/4**
- actions the person asked for: ['change_setting']

Abandonment, verbatim:
- `coordinator_under_pressure`: “Nothing appeared in the chat—if a tool doesn't work on load, I don't have time to debug it.”
- `artist`: “This is about committee governance and foundation stuff, not a word about my shots or dailies feedback.”

## 6. What v0 does not model

Calendar reality (holidays, timezones, meetings people skip); the terminal UI beyond the click; IT provisioning and the tenant admitting the bot; anybody telling a colleague out loud; transcription quality. Quality enters only through what the mails actually say. The organizer's invite is admitted as a direct fact rather than a parsed ICS — the inbound mailbox double landed on a sibling branch during this run and feeds the founder's lane, not the sim's. The fixture is a DNA/ASWF working session, not a dailies review: no dailies transcript exists yet, and the personas judge CONTENT, so this is the single biggest gap between this measurement and SPI's real pilot.
