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

## Real meetings for a `live_probe`

A `live_probe` needs a real meeting on the platform it tests. Create one with
`core/meetings/modules/join/scripts/platform-meetings.ts` (#1970), run from the join module with `tsx`:

- `tsx scripts/platform-meetings.ts create <google_meet|zoom|teams>` creates a meeting with the test
  accounts and prints a JSON receipt (platform id, native meeting id, meeting URL without passcode).
- `tsx scripts/platform-meetings.ts delete <receipt.json>` deletes it after the probe, checks that it is
  gone and prints the receipt with `deleted` filled in. Exit 1 means it was not deleted.
- `--dry-run` prints the planned requests and the receipt shape without credentials.

Credentials come from `VERIFY_SECRETS` (default `~/vexa-test-rig/secrets.env`, outside the repo). They
are test-rig inputs, not product settings. The full input list and the per-platform delete semantics
(Meet has no space delete, so the conference is ended and the event cancelled) are in
`core/meetings/modules/join/scripts/README.md`.
