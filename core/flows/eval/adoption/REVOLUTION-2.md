### Revolution 2 — cohorts, synthetic dailies, and a mail-text result that reversed under correction

**Line: `244d6391e`.** Engine restarted **00:42:17Z**, poller **00:42:24Z** (no meeting live, no `dna-*` reaction in flight). `GET /flows` → `post_meeting v1 [require_workspace, process_meeting, email_minutes, email_attendees]`, 20 steps, and `_provenance` is live in the running source. This was one merge, so **one re-baseline**, as promised.

#### 1. The relevance bottleneck was the instrument, and fixing it made the instrument discriminate

Every persona in r1 judged one ASWF TSC governance meeting. Now each cohort is judged only on meetings it would actually attend — `insider` (pipeline/studio-technology, the DNA/TSC world) on the recorded fixtures, `production` (coordinators, PMs, supervisors, artists) on **generated dailies**, one per show, marked `synthetic: true`, under `~/dna-fixtures/synthetic/`, seeded through `meeting_seed` via the normal caps path so no part of the seeding leg is special-cased.

The generated dailies are convincing where it counts — the resulting attendee mail reads: *"Lighting review of six Show B shots (LI59 sequence). Two shots approved with a note, two to retake, one to notes-and-resubmit, and LI59_125 surfaced as unsubmitted — now critical path for Friday's 9 AM client review."*

**H1 vs the null, per cohort** (peak / steady active, ret30 / ret90):

| cohort | n | lever | peak | steady | ret30 | ret90 |
|---|---|---|---|---|---|---|
| insider | 234 | null | 1.48% | 1.43% | 1.00 | 1.00 |
| insider | 234 | **A shared** | 7.39% | 1.99% | 0.59 | **0.31** |
| insider | 234 | **B personal** | **10.34%** | **7.69%** | 1.00 | **0.82** |
| production | 1,766 | null | 0.17% | 0.06% | 0.33 | 0.33 |
| production | 1,766 | A shared | 1.36% | 1.26% | 0.93 | 0.85 |
| production | 1,766 | B personal | **1.70%** | **1.39%** | 0.93 | 0.93 |

**H1 holds in both cohorts — 5–10× the null on peak active.** And **A vs B separates, reversing r1's "A ≈ B"**: in the insider cohort the shared variant *acquires but bleeds* (steady 1.99%, ret90 0.31) while the personal variant *acquires and holds* (steady 7.69%, ret90 0.82). r1 could not see this because every persona was reading a meeting they had not attended, so nothing could retain. **T_full remains unbounded at every threshold in every arm.** Caveat: insider n=234, so its retention figures rest on a few dozen people.

#### 2. The mail-text change — and the measurement of it that I had to throw away

First result: `acted 4.8% → 27.0%`. It was wrong, and wrong in my favour. `replied` is one of the UI actions that make a person "active", and the provenance change had just added *"Reply 'no minutes' and I will stop sending you them"*. Verbatim from the after arm:

> *"I replied 'no minutes' to stop the emails; I have two minutes between dailies and this system keeps sending me notes about meetings I'm not running."*

That is a person **leaving**, scored as adoption. The change was being credited for the use of the unsubscribe it had itself introduced. `opted_out` is now its own field, excluded from `active_action`, forced to `outcome=ignored`, and reported as its own column. Corrected, same personas, same fixture, only the two lines differing:

| arm | open | acted | **opted out** |
|---|---|---|---|
| before (no provenance) | 61.9% | 12.7% | 6.3% |
| after (provenance) | **84.1%** | 14.3% | **27.0%** |

**The lines make people open it and then leave.** Opening rises sharply, action barely moves (n=63; 12.7→14.3 is inside noise), and explicit opt-out **quadruples**. On this sample the change converts silent ignoring into deliberate unsubscribing — which is better information and worse adoption.

**Confound, stated because it is material:** this A/B ran on *insider-fixture* text across *all* personas, so "you told me why, and it turns out it isn't for me" is mixed into the 27%. A clean test needs the same before/after on each cohort's own mail; the production cohort has no pre-provenance dailies mail to compare against, so that costs another probe run. **I would not ship the opt-out line on this evidence.** My read: keep line 1 (why this arrived — it is what the coordinators asked for), and treat line 2's opt-out invitation as a separate decision measured per cohort on relevant mail. That is a founder call, not mine.

#### 3. Third correction to the instrument in two revolutions

After the 38% silent judge failures and the absorbing-zero rates, this is the same class again: **a defect that produces a plausible number rather than an error**. All three inflated or flattened a result while looking clean. The pattern is worth naming — every one was caught by reading the verbatim `why` beside the number, never by looking at the number.

Also fixed this round: dailies pacing (a 100-line review rendered as 7.8 minutes because timing came off speech length alone and ignored the silence while a shot plays — now 14–28 min, `retime()` re-paces fixtures on disk without re-generating).

#### 4. Open, and yours

- **`meeting_seed` should set `scheduled_at` from the fixture occurrence.** `_meeting_stamp` currently falls through to `created_at` for seeded rows, so two occurrences separate only because their creation times differ — correctness by luck. Test records the gap; the rig is yours.
- **The opt-out line decision** above.
- **`~/dev/minutes-ui` is 139 commits behind origin** with another session editing `clients/terminal` in it.

<!-- vexa-agent -->
