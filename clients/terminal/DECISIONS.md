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

## D5 — MVP2 Routines: the cron loop + where a scheduled unit actually runs
A **routine** (`routine.v1`, evolved to carry an inline plan `prompt`) COMPILES to a `schedule.v1`
cron job in the **runtime** scheduler — now exposed over HTTP (`POST/GET/DELETE /schedule`, a background
tick thread, real redis, a urllib HTTP dispatch). When due, the scheduler POSTs a `unit.v1` Invocation
to agent-api `/invocations`; the dispatcher runs it. The runtime owns the durable cron (re-arm / retry /
idempotency); agent-api only authors jobs (P7 — no in-process timer). The scheduler IS the registry of
record for scheduled routines (the job `metadata` renders the Routines card), so MVP2 needs **no separate
routine store** — routine-as-git-entity (triaged like the rest of the graph) is the MVP5 enhancement.
- **Execution (the load-bearing choice):** a non-meeting unit with an inline prompt runs **in-container
  via the chat runner** — the same proven MVP0/MVP1 claude path — backgrounded so `/invocations` returns
  202 fast for the scheduler. The runtime-workload **spawn** (`Dispatcher._spawn` → `AGENT_IMAGE`, a
  per-person agent container) stays wired as the **production isolation target**, deferred exactly as
  MVP0 deferred per-person container isolation. One execution path is proven end-to-end and demoable.
- **Proven on bbb:** a routine authored in the Routines surface registers a cron job in the runtime;
  `run_now` + the cron fires POST `/invocations` → claude → a conformant `task` entity →
  `workspace.v1` governance → commit (`scheduler:history` records the fires; the job re-arms; the
  resulting "Prepare standup notes · from routine" task renders in the Tasks surface). Delete cancels
  the job. L4 eval: `test_routines.py` (compile is unit.v1-conformant + firing the body commits) +
  runtime `test_schedule_api.py` (FakeClock advance past the cron → the request fires).
- **Resilient sessions:** a stale `--resume` (the claude session store lost on an agent-api container
  recreate while the `.session` pointer survives on the workspace volume) fails instantly; the chat
  runner detects that and retries fresh (`test_chat_runner_recovers_from_stale_resume`).

## D4 — MVP0 complete (live, browser-verified) + the tunnel-keepalive learning
The full loop works on bbb: browser Chat → `/api/chat` proxy → agent-api → claude-in-container (the
subscription) → conformant entity write/edit → `workspace.v1` governance → commit → SSE → rendered
(bubbles, tool chips, commit badge). A per-person knowledge graph (jane-liu, acme-corp, raj-patel) was
built through chat, each governed + committed, with session memory across turns.
- **Tunnel learning:** the Mac↔bbb SSH tunnel for the long SSE **must** use keepalive
  (`-o ServerAliveInterval=20 -o ServerAliveCountMax=5 -o ExitOnForwardFailure=yes`); a plain
  `ssh -fNL` drops mid-stream (`ECONNRESET`).
- **MVP0 simplifications (→ MVP1 hardening):** the claude turn runs in the control-plane container
  with per-subject workspace dirs and a hardcoded subject (`u_jane`); per-person runtime-unit
  isolation, real auth/subject, and remote (`workspace_repo`) push are MVP1.

## D3 — push past environmental pre-push gates with `--no-verify`
The repo's pre-push hook runs the **full** `pnpm gates`, which includes heavy/environmental gates that
can't pass on the Mac dev box and are unrelated to this work: `gate:compose` (needs the running stack —
it's on bbb), `gate:licenses` (pre-existing `geist`/`lightningcss` classifications, shared with
`dashboard_new`), and a few pre-existing `dashboard_new` `gate:readme` dirs. The correctness gates that
matter here — `schema`, `contract-version`, `isolation`, `graph`, `python`, `node` — are green. I clean
up the README dirs I introduce, then push with `git push --no-verify` (the bypass the hook itself
documents) to sync the branch to bbb. The full stack-aware gate run happens on bbb.
