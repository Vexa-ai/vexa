# ADR-0026 — Boundary contracts across the three planes

**Status:** Proposed · 2026-07-04

## Context

The v0.12 control plane is three planes — meetings (meeting-api), kernel (runtime), agent
(agent-api) — each consuming deployment configuration from four surfaces (compose, helm, lite,
plus raw env for direct runs). Nothing bound those surfaces to what the code actually reads, and
three incidents in one release window showed the cost, each a different failure class:

1. **Absent config, discovered at request time.** A fresh compose stack answered `POST /bots` with
   a runtime 503 — *"transcription requested but the STT service is not configured
   (TRANSCRIPTION_SERVICE_URL/TRANSCRIPTION_SERVICE_TOKEN unset)"*. The stack had booted green;
   it had no way to declare or surface its own config completeness.
2. **Set-but-broken config, discovered never.** STT keys were SET but the token was rejected
   (401) by the transcription workers: the bot joined, the meeting showed live, audio was
   captured, every transcription request 401'd — and nothing surfaced anywhere except worker logs.
3. **Set-vs-absent host coupling, discovered inside a spawned container.** `HOST_CLAUDE_CREDENTIALS`
   unset (or pointing at a missing host file) produced *"Model inference failed: Not logged in"*
   inside a per-dispatch agent-worker — because docker bind-mounts a missing HOST path as an empty
   directory, and the mount mechanism lived only in runtime source, undocumented on any surface.

Config drift had the same shape as contract drift: an undeclared coupling between components that
only a runtime failure reveals. The repo already has the cure for contract drift — published
`X.vN` contract dirs (schema + goldens + validate.mjs), sealed by hash in `contracts.seal.json`,
held by gates. This ADR extends that discipline to deployment config.

## Decision

### 1. config.v1 — a sealed contract like the others

`deploy/contracts/config.v1/` publishes the shape (`config.schema.json` + goldens + `validate.mjs`,
sealed in `contracts.seal.json`). Each adopted service ships a `config.v1.json` **declaration**
next to its code (vendored into its image): every env key it consumes, classed as

- **required-explicit** — boot fails loud if unset (one `ConfigError` naming every missing key);
- **defaulted** — the documented code default applies;
- **capability** — the key belongs to a named optional capability with a **tri-state** computed
  from the env: `configured` / `not_configured` / `misconfigured` (mode=all: some-but-not-all
  member keys set; mode=any: alternative paths, ≥1 suffices). The service runs either way;
  capability-gated endpoints fail loud with a typed, actionable error naming the unset keys.

A capability may declare a **live probe** — one cheap verification that SET values actually work,
because incident 2 proved env presence is not configuration: an authenticated HTTP call (an
unauthorized answer or network failure ⇒ `misconfigured`; any other status proves the credential
was accepted) or a credentials-file check (a directory where a file should be is docker's
missing-host-path signature ⇒ `misconfigured`). Probes run at boot (logged, never boot-blocking)
and lazily on `/health` under a declared `ttl_s`; they never flip the service's `status`.

### 2. One shared preflight, vendored verbatim

`deploy/contracts/config.v1/preflight.py` is the canonical validator; each service vendors it
byte-identically as `config_preflight.py` (the gate enforces equality). The planes share the
contract, not a package dependency — the same isolation stance as the schema-by-path loading the
services already do (P2). Boot hooks: meeting-api `__main__._require_config` (which already
fail-fasted on ADMIN_TOKEN — now declaration-driven), runtime `build_production_app`, agent-api
`_build_production_app`. Env-level state is computed at call time; no boot snapshot.

### 3. /health carries the capability rows — additively

Each adopted service's `/health` gains a `capabilities` object:
`{"stt": {"state": "misconfigured", "probe": {"ok": false, "reason": "unauthorized — …"}}}`.
Existing consumers (compose/helm healthchecks, tests) key on `status`/`checks` and keep working;
capability rows never change the status code — an unconfigured capability degrades a **feature**,
not the process. Incident 2's silent 401 is now a red row on `/health` before any meeting runs.

### 4. gate:config-contract — the surfaces cannot drift

Per adopted service, CI proves: (1) the declaration conforms to the sealed schema; (2) the vendored
preflight is canonical; (3) every declared key appears in each deploy surface its `targets` names
(compose env block / `.env.example`, helm deployment template, lite supervisord/entrypoint);
(4) every key a surface sets is declared — or carried in the declaration's `surface_only` list with
a reason (documented drift, e.g. the runtime's unread `INTERNAL_API_SECRET`, discovered by this
gate); (5) every literal `os.getenv`/`os.environ` read in the service's source names a declared
key. The allowlist is process plumbing only (`PYTHONPATH` etc.), kept in the gate, tight.

### 5. The canonical gate + the deterministic credentials path

The STT spawn guard (`meeting_api/bot_spawn/router.py`) is the canonical capability gate: it
consults `capability_state("stt")` from the same declaration that drives boot and `/health` —
same external behavior (typed 503, actionable message), one source of truth. For incident 3,
`HOST_CLAUDE_CREDENTIALS` is now explicit and deterministic end to end: the declaration +
`.env.example` document the full mechanism (a DOCKER-HOST path, bind-mounted read-only into every
agent-worker at `/root/.claude/.credentials.json`; alternatives: `ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` brokered as env), compose mirror-mounts the path into the runtime container
at `/var/lib/vexa/host-claude-credentials`, and the `model_inference` file probe verifies the file
exists and parses — so a bad path is a `misconfigured` row at boot, not a chat-time mystery.

## Trade-offs

- **Verbatim vendoring over a shared package** — three identical files kept honest by a byte-equality
  gate, instead of a cross-domain dependency that would break the plane isolation the graph gates
  hold. The cost (sync-by-gate) is the same one the repo already pays for schema seals.
- **Declarations describe reality, including its warts.** `surface_only` documents keys surfaces set
  that code never reads rather than silently allowlisting or eagerly deleting them; removal rides a
  separate deploy-surface cleanup with its own review.
- **Probes are advisory, not gating.** A probe failure marks `/health`, it does not block boot or
  (today) the spawn path — a transient STT blip must not brick spawns. Extending the spawn guard to
  consult the cached probe verdict, and adding probes for further capabilities (object_storage,
  bot_gateway), are deliberate follow-ups.
- **Adoption is per-service opt-in** (a declaration + the vendored preflight + a row in the gate's
  `CONFIG_ADOPTED` table). The three planes adopt now; gateway/admin-api can follow the same recipe.
