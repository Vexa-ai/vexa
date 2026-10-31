# test-scenarios — the seam/module scenario catalog

One concern: a committed, behavior-named catalog of the failure scenarios the 0.12 hardening campaign
pins as repeatable seam/module tests. Each catalog is one row per known failure class, naming its
`module_probe` / `seam_probe` (and optional `compose_probe` / `live_probe`) and the EXACT expected
contract state. The catalogs are the campaign checklist + standing anti-regression ledger (see
the maturity-findings ledger in the maintainer workspace, and the campaign plan).

Two catalogs, split by surface:

- **`meeting-seams.md`** — the **meetings-internal** failure classes (admission, recording, lifecycle,
  stress, gateway rate-limit) that live entirely inside the meetings domain.
- **`terminal-seams.md`** — the **terminal integration surface**: the terminal↔{meetings,agent} seams
  (live transcript fidelity, catch-up cursor / gapless reconnect, multi-meeting isolation, processing
  opt-in, complete mediation, fault surfacing, bot-action round-trip; plus a deferred judge tier for
  processed-notes / cards / tags / research-commit). Documents two rigs — Rig A (data-plane: redis
  carrier → agent-api SSE) and Rig B (SSE frames → `LiveTranscriptEngine` render).

A row is `green` ONLY after opening the named probe and confirming it asserts the expected state (P8) —
never on a similarly-named test's mere existence.

Depends on: nothing (documentation). Consumed by humans + the autonomous hardening session.
