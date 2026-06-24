# Terminal — autonomous build decisions log

Record decisions and blocker-bypasses so nothing is silently dropped (per the plan's autonomy policy).

## D0 — code sync = git, not rsync
Edit on Mac → commit → `git push -u origin feat/terminal-mvp0` → on bbb `cd ~/vexa-0.12 && git stash &&
git fetch && git checkout feat/terminal-mvp0`. Build/run on bbb (the live 0.12 deployment). Tracked,
no clobber.

## D1 — new `unit.v1` over `invoke.v2`
`invoke.v1` is **sealed** (`contracts.seal.json`) and meeting-shaped (`meeting` required, titled
"meeting → agent trigger"). Three of four triggers have no meeting. Cut a new
`core/agent/contracts/unit.v1` (the universal invocation taxonomy) and leave `invoke.v1` frozen for the
meetings path (retired later) — vs. a breaking reshape of a sealed contract. (Confirmed direction with
the human; proceeding autonomously per the goal directive.) See `docs/FOUNDATION.md`.

## D2 — sealing cadence during the build
New contracts (`unit.v1`, `routine.v1`, `task.v1`, `tool.v1`, `proactive-card.v1`) are created
**UNSEALED** during active dev — `gate:schema` validates their goldens, `gate:contract-version` reports
them unsealed (green-on-empty path), so the build stays green without a premature freeze. Edits to
*already-sealed* contracts (`ws.v1`, `api.v1` shapes, `identity.v1`) are **deferred to the MVP that
needs them** and re-sealed then (`pnpm seal:contracts`, a `lane:contract` step). MVP0 chat streams SSE
directly over the `/api/chat` HTTP response, so it needs no `ws.v1` change.
