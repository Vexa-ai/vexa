# v0.12.18 — dev:builds verdict

Date: 2026-07-23 · Head: `ca3b7927` (final — 21 PRs) · **VERDICT: GREEN**

## Evidence

| leg | result | note |
|---|---|---|
| Full gate suite (`gates.mjs all`, CI mock config) | ✅ green | run twice at the final head; 33 gates incl. python (12 pkg) · node (18 pkg) · contract-conformance |
| `gate:compose` — real stack | ✅ "proven bot-ready (health·auth·transcript·recording·control-plane)" | at `ca3b7927` |
| Lite image build from the release SHA | ✅ `vexa-lite:dev` 7.54GB | registry buildcache; lint warnings pre-existing |
| Lite boot + schema converge | ✅ | **#931 acceptance**: admin-api boots on pinned Python 3.12 (crashed at boot pre-fix) |
| Lite front doors | ✅ gateway :8056 · agent-api :8100 · terminal :3001 | `make -C deploy/lite test` |
| Lite concurrent-bots smoke | ✅ "PASS: 2 concurrent bots launched on isolated profiles, 45s stable" + #585 stream-authz | `concurrent-bots.sh` |
| Touched-module replay harnesses | ✅ | covered by gate:node (bot replay/mock suites) + gate:python + gate:eval |

## Deviations hit and their recoveries (for retire:retro)

1. **Ghost half-created container wedges the compose gate** — a force-killed
   `docker compose up` can leave a NAMELESS container labeled into the project
   that `compose down` won't remove and `docker rm -f` can't (daemon/containerd
   desync: listed in `ps -a`, "No such container" on operate). Recovery without
   a daemon restart (which would kill peer sessions' stacks): re-run under a
   fresh **`COMPOSE_PROJECT=`** name — the conftest supports exactly this
   override "on a shared host". The ghost stays until the next daemon restart.
2. **Peer-session stacks share the daemon** — another session ran a full
   `vexa-v012` stack (from `vexa-flatq`) during these builds. Dynamic ports
   held; heavy builds were serialized; never `docker builder prune` or
   restart the daemon while a peer stack is live.

## Eligibility

Green → eligible for `witness` + stage per the skill's bar. The witness map is
`DEV-PLAN.md` §2 (two batched human sessions; #856/#932 landed, so the Meet
session is unblocked). Stage claim via `stage-own`; `dev:rebase` check before
deploy.
