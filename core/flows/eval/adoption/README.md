# `eval/adoption` — the adoption simulator

The objective function of the self-improvement loop: **how many simulated DAYS until an org
reaches full adoption, and does the adoption hold.** The product is real — a real flows engine,
real mails, a real agent. Only the people are simulated.

**Active** is the whole point and it is strict: in the trailing 14 days the person **opened** a
Vexa mail **and** took a **UI action** — clicked into the terminal, sent a chat turn, replied to
the mail, or put the mailbox on a meeting they own. Delivered mail counts for nothing; an open
alone is *reached*, not active.

## The two layers, and why

    layer 1  SAMPLE      real identities -> real flows -> real mail text -> one Haiku call per
             (sample.py) touch. A touch the product does not send cannot be sampled, which is
                         why the attendee follow-up had to be BUILT before it could be measured.
    layer 2  EXTRAPOLATE the whole meeting graph walked daily on those measured rates.
             (sim.py)

Nothing in `personas.py` asserts a propensity: the numbers are measured from the answers, or the
simulator would only recite its own priors.

## Files

| | |
|---|---|
| `org.py` | the org generator — people, units, and the **meeting graph**. Profiles: `spi` (Sony Pictures Imageworks; dailies are the dominant recurring meeting) and `bank`. Size, structure and cadence are parameters. |
| `personas.py` | the seven personas, the role/department skews, and the answer schema |
| `judge.py` | one Haiku call per touch, and the persona side of the agent conversation |
| `sample.py` | harvest real mail → judge → `rates.json`; and the real 3-turn chat with the deployed agent |
| `sim.py` | the daily dynamics: adoption curve, T_full, retention 30/90, steady state, churn reasons |
| `simlane.py` | the door into the sim's own flows lane |
| `probe*.py` | the evidence probes; `probe3` is the before, `probe4` the after |

## The sim lane — why there are two flows engines

The founder's hot lane (`:18200`, database `flows`) admits **real** invites from a **real**
inbox. Turning attendee fan-out on there could mail real people. So the simulator runs its own
worker + api (`:18201`, database `flows_sim`) from this worktree, with the mailbox integration
deliberately never started, and an allow-list of three test domains. Start it with
`~/.storm/sim-flows-up.sh`. It shares agent-api, gateway, admin-api and mailpit — all of which
are per-identity safe. It never deletes a mailpit message.

## Running

    ~/.storm/sim-flows-up.sh                 # the isolated lane
    python3 probe4.py shared                 # end-to-end: does an attendee get anything?
    python3 sample.py                        # -> $SIM_RUN_DIR/rates.json
    python3 sim.py rates.json spi 2000,20000,200000

## Its touches are SUBSTITUTED — the real-click primitive lives next door

Say this plainly because the numbers do not: **this package does not click anything.** `sample.py`
and `converse_run.py` compose the opening in Python and POST it to agent-api (`:18500`) with a
hand-set `X-User-Id`; `probe.login` mints the identity through the MCP's `start_onboarding` /
`confirm_login` code pair. No link is read out of a mail, no browser session exists, no cookie is
ever held. So every defect between *the link the product mailed* and *a primed chat* — a dead
deeplink, a redeem that sets no cookie, an unreadable preset, a share that fails to redeem — is
invisible here, and a run says "touched" anyway. `probe_click.py` is the closest thing in this
package and it still stops short: it signs in over the MCP, not over the mailed magic link.

The real-click primitive is **`click_link` in `../dna/replay.py`**. It takes the mail dict
`mail_search` returns plus the recipient address and performs the actual hop: the link out of the
delivered body → `POST /api/auth/request-link` → the sign-in mail read back out of mailpit →
`GET /api/auth/redeem` with a cookie jar (which is where the user row is created) → `GET
/api/auth/me` to assert the session → `?tshare=` redeemed through
`/api/transcripts/share/accept` → the preset body read through `/api/workspace/file` → the turn
POSTed to the terminal's own `/api/chat` and read back from that session's history. Zero
dependencies (`http.cookiejar` + `urllib`); no Playwright. The one leg it still simulates is the
React composition of the opening, and its docstring says so.

**To adopt it here** — deliberately not done in this change, because it moves the measured rates
and a rate that moves for a harness reason is worse than one that is honest about its method:

1. Point the sim at the public terminal (`VEXA_DNA_TERMINAL`), not a loopback origin — the redeem
   cookies are `Secure`, so a `127.0.0.1` origin silently drops them and the click reads as a
   product failure.
2. Replace `probe.login` with the magic-link hop, and take the uid from the `vexa-user-info`
   cookie instead of `confirm_login`. Personas then mint identities the way strangers do.
3. Replace the hand-composed openings in `sample.py` / `converse_run.py` with `click_link(mail,
   addr)` on the mail the persona actually received, and feed `judge.py` the returned `reply`.
4. Re-baseline: publish the first post-adoption `rates.json` beside the last substituted one and
   say in `REVOLUTION-N.md` that the method changed. **The number is relative between
   revolutions**, and this changes what it is relative to.

## What this does NOT model

Calendar reality (holidays, timezones, meetings people skip), the terminal's own UI beyond the
click, IT provisioning and the tenant admitting the bot, anyone talking to anyone out loud, and
transcription quality. Quality enters only through what the mails actually say.

**The number is relative between revolutions. It is never a forecast.**
