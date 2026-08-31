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
3. **real adapters** — swap fakes for the meeting-api/agent-api HTTP steps; same witness script,
   same assertions (not yet wired).

The witness proves what the storm cannot: real time passing, a real SMTP conversation, and a
human judging that the artifacts are the ones a user should see.
