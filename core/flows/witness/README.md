# witness — the flows engine, watched end to end

The witness iteration ladder (each rung keeps the previous one's instrumentation):

1. **doubles, automated** — `python3 witness/run.py`: real SystemClock (seconds-scale timings),
   sqlite, the loopback world as bot/provider double, email to a DOUBLE (Mailpit when
   `localhost:1025` answers, else `.eml` files under `witness/outbox/`). The run narrates every
   observable — facts admitted, reaction transitions, receipts, emails — and exits with a verdict.
2. **doubles, human in the loop** — same run with Mailpit up (`docker run -p 1025:1025 -p 8025:8025
   axllent/mailpit` or the dev stack's instance): the human watches the narration, opens the
   Mailpit inbox at http://localhost:8025, confirms ONE confirm mail and ONE summary mail per
   inside-domain recipient, and that the summary cites the commit.
3. **real adapters** — `run_real.py` / `run_live.py`: real bot, real agent, real mailbox; only the
   audio stays a fixture.

The witness proves what the storm cannot: real time passing, a real SMTP conversation, and a
human judging that the artifacts are the ones a user should see.

## series_run.py — the scaffold-inference iteration loop

A different axis from the ladder above. Rungs 1–3 ask *does one meeting work end to end*.
`series_run.py` asks **does the system infer what is going on across a series of meetings** — the
daily-meeting smoke: scaffold a desk fresh from episode 1, ask only the load-bearing questions,
then let episodes 2 and 3 arrive and see whether the desk knows the people, the projects, the
vocabulary and the running threads.

    python3 witness/series_run.py list
    python3 witness/series_run.py reset --series nodejs-tsc
    python3 witness/series_run.py run   --series nodejs-tsc --through 3
    python3 witness/series_run.py judge --series nodejs-tsc --episode 1

Episodes are REAL public recurring meetings with organizer-published notes as ground truth —
`tests/series/`, one directory per series with its own README and provenance. Each episode is
admitted as a `meeting.completed` fact carrying that episode's transcript: the same fact
production's `post_meeting` reacts to, built the same way `flows_steps/meeting.py`'s existing
fixture path builds it.

**Offline by construction.** The harness embeds no model. It probes agent-api; if it answers, the
scaffolding/minutes phases run as real agent turns through `flows_steps/agent.py`, and if it does
not, those phases are SKIPPED and the run says exactly which ones and why. Everything else —
admission, the engine, receipts, waits, the durable sqlite, the fixture load — runs either way.
`--agent skip` forces the offline path.

**A probe is not a credential.** agent-api answering says nothing about a model credential
existing behind it: an uncredentialed tier accepts the turn and never replies. That is the
TIMEOUT path, and `--agent-timeout` is how long you are willing to wait for it.

**`judge` does not score.** It renders the organizer's notes and our artifacts side by side, plus
a substring presence check against the ground truth's `## Entities` list, and asks a human the
question. A number here would be a claim we cannot support.

State lives in `witness/series_state/<slug>/` (gitignored) and `reset` is the only way back to a
fresh desk — that is what makes "scaffold fresh from episode 1" honest rather than a figure of
speech.
