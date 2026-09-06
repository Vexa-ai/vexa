# Revolution 6 — what it takes to execute the seed

The pilot runbook input. Revolution 5 found that the only strategies reaching full adoption are
(iii) seed every show's production office and (iv) have an admin put the mailbox on every
recurring dailies. Both are only real if somebody can do them on the product as it stands. This
measures what each costs today, on the stack, and closes the gap that was cheapest to close.

---

## The table

| | seed (iv) — admin puts the mailbox on every dailies | seed (iii) — onboard every production office |
|---|---|---|
| what the admin/person does | hand the product a list of recurring meetings | each person holds an email conversation |
| **cost BEFORE this revolution** | **20 dailies = 20 separate actions.** No verb accepts a list — `POST /events` takes one fact, `bot_schedule` one meeting, forwarding one ICS. No receipt over the set. | **2 human email replies per person**, and nothing arrives until they are made |
| **cost AFTER** | **one call, one list, 0.06 s for 20**, a row per meeting, re-postable | unchanged — this one is not a mechanics gap |
| machine time | negligible either way (0.1 s for 20 singular calls) | 76 s to the first onboarding mail; 391 s end-to-end with an instant human |
| parallelises? | n/a — it is one call | yes, machine-side: every person is an independent reaction |
| what actually bounds it | nothing, now | **the humans.** 200 people ≈ 400 email replies |
| gate | admin key | none needed |

**The headline for the pilot: (iv) is now a single paste, (iii) is 400 human replies.** At 200
people the admin route is the one that can be executed on a Monday.

---

## 1. Seed (iv): measured, then built

**Measured first.** Every route the product offered an admin was singular:

| route | calls for 20 dailies | accepts a list? | receipt over the set? |
|---|---|---|---|
| forward the ICS by hand | 20 mails | no | no |
| `bot_schedule` from the chat | 20 calls | no | no |
| `fact_emit` / `POST /events` | 20 calls (0.1 s total) | no | no |

The machine time was never the cost. **The absence of a plural was**, and with it the absence of
any way to tell an admin *which* of twenty meetings failed.

**Built:** `POST /events/batch` on flows-api — admin-key gated, takes the pasted list, admits one
fact per meeting through the same `admit()` and the same per-(fact, flow) dedup key as the
singular endpoint. It is still not a step-runner: what each fact causes stays the registry's
business.

```
ONE CALL, 0.06 s -> {'submitted': 20, 'admitted': 20, 'duplicates': 0, 'failed': 0}
re-post the same list -> {'submitted': 20, 'admitted': 0,  'duplicates': 20, 'failed': 0}
```

It returns a **row per meeting**, not a count: a partial success is the normal case — one bad url
in twenty — and a bare number cannot tell an admin which meeting to fix. Re-running is a no-op
per meeting, so an admin who pastes their list twice does not double-invite anyone.

**A gate finding on the way.** `fact_emit` and `flows_submit` are guarded by `me()` alone — any
*authenticated* user may inject a fact naming an **arbitrary organizer**, or change the org's flow
definitions. Those are operator verbs behind an authentication check, not an authority check. The
new batch endpoint is admin-key gated; the two existing verbs are not, and that is the same gate
the operator row owes.

## 2. Seed (iii): measured, and it is not a mechanics gap

End to end on a person who had never touched the product:

```
t+0     invite.received
t+4s    "Accepted: Show A dailies"
t+8s    "Prepare: Show A dailies"
t+76s   the onboarding conversation mail          <- one agent turn
        ... 2 human replies ...
t+391s  .scaffolded exists
```

**2 human replies per person.** `.scaffolded` is written by the *agent*, only after that
conversation, and `require_workspace` blocks every minutes mail until it exists — so a person who
never replies never receives anything, and the flow nudges them every 15 minutes forever.

391 s is with a simulated human replying instantly. The machine side parallelises — each person is
an independent reaction — so **wall clock for 200 people is bounded by the humans, not the
product**: ~400 replies, realistically days. There is no mechanics fix here; the fix is either
fewer required replies (B4: onboarding in the chat, one marker) or not needing (iii) at all,
which is what (iv) buys.

## 3. `trust_quality`: the name is not the evidence

presence-only, nobody mailed, nobody told (n=48 per arm):

| arm | will_add | looked at what it produces | coordinators |
|---|---|---|---|
| plain name — "Vexa Minutes" | **0.0%** | 0.0% | 0.0% |
| name carries where to look | 2.1% | 0.0% | 8.3% |
| that **+ the day-1 minutes mail to the whole dailies** | **12.5%** | **35.4%** | **41.7%** |

**The name alone does almost nothing** — 0% → 2.1%, and nobody goes and looks. The name *plus*
delivered evidence on day one moves the people who can actually act from **0% to 41.7%**, and a
third of them go and check the output.

The blockers move as the trust question is answered: `trust_quality` 19 → 5, `unclear_how` 19 →
12, while `permission` rises 0 → 10 and `not_my_call` holds at 12. **Once they believe it works,
what stops them is authority, not doubt** — which is precisely what seed (iv) routes around,
because an admin already has the authority.

## What this changes about the runbook

1. **Prefer (iv).** One paste, admin-gated, idempotent, no per-person onboarding.
2. **Make day one carry the evidence.** The bot's name pointing at the notes is worth almost
   nothing on its own; the first minutes mail reaching the whole dailies is what converts.
3. **(iii) is a people-plan, not a product plan.** 400 replies for 200 people. Budget it as such,
   or reduce the two required replies first.

Everything above was measured on the stack. The number is relative between revolutions and is
never a forecast. The invite wording and the From-address/watched-mailbox split remain founder
decisions and are untouched.
