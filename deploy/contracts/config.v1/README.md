# config.v1 — the per-service deployment-config contract

**Concern:** the machine-readable answer to *"is this deployment's configuration complete, and if
not, what exactly is missing?"* — asked **at boot** (fail loud, P18) and **at runtime** (typed
capability errors + `/health` rows), instead of discovered one incident at a time. The motivating
incidents, one per failure class: (1) a fresh stack answered `POST /bots` with a runtime 503
*"transcription requested but the STT service is not configured"* — absent config, discovered at
request time; (2) STT keys SET but the token 401-rejected — bots joined, audio captured, zero
transcripts, nothing surfaced; (3) `HOST_CLAUDE_CREDENTIALS` unset/dangling — *"Model inference
failed: Not logged in"* inside a spawned worker, mechanism documented nowhere. See ADR-0026
(*Boundary contracts across the three planes*).

**Surface:** `config.schema.json` (the shape) · `golden/` (the spec, P8) · `validate.mjs`
(gate:schema; also validates the LIVE service declarations via `--file`) · `preflight.py` (the
canonical shared boot validator every adopted service vendors verbatim).

## The declaration

Each adopted service ships a `config.v1.json` next to its code (vendored into its image), declaring
**every environment key it consumes**, one of three classes:

| class | boot behavior | runtime behavior |
|---|---|---|
| `required-explicit` | unset/empty ⇒ `preflight()` raises `ConfigError` naming every missing key — the deploy **refuses to boot** | n/a (it booted, so it is set) |
| `defaulted` | nothing to enforce — the documented `default` applies | n/a |
| `capability` | never blocks boot | the named capability's **tri-state** is computed from the env: `configured` / `not_configured` / `misconfigured` (mode=all: some-but-not-all member keys set; mode=any: alternative paths, ≥1 suffices). Capability-gated endpoints consult `capability_state(...)` and fail loud with a typed, actionable error; `/health` carries a `capabilities` object (ADDITIVE) |

Extra fields: `secret` (a credential, P14 — never logged/goldened), `targets` (which deploy
surfaces plumb the key — `compose`/`helm`/`lite`; **empty array** = an in-code dial deliberately not
exposed by any surface, still declared so the undeclared-read scan stays tight), and a top-level
`surface_only` list (keys a surface sets that the service does NOT read — documented drift with a
`reason`, so the check never silently ignores anything).

```json
{
  "contract": "config.v1",
  "service": "meeting-api",
  "keys": [
    { "key": "ADMIN_TOKEN", "class": "required-explicit", "secret": true,
      "description": "HS256-signs the per-spawn MeetingToken; unset, every POST /bots 500s" },
    { "key": "REDIS_URL", "class": "defaulted", "default": "redis://redis:6379/0",
      "description": "segment stream + status pub/sub" },
    { "key": "TRANSCRIPTION_SERVICE_URL", "class": "capability", "capability": "stt",
      "description": "STT endpoint the spawned bot transcribes against" }
  ],
  "capabilities": {
    "stt": { "description": "spawned bots can transcribe", "mode": "all",
             "when_unconfigured": "POST /bots with transcribe_enabled=true answers a typed 503",
             "probe": { "kind": "http", "timeout_s": 2, "ttl_s": 60,
                        "http": { "method": "POST", "url_key": "TRANSCRIPTION_SERVICE_URL",
                                  "path": "/v1/audio/transcriptions",
                                  "auth_key": "TRANSCRIPTION_SERVICE_TOKEN",
                                  "unauthorized_statuses": [401, 403] } } }
  }
}
```

## Live probes — set values must also WORK

Env presence is not configuration (incident 2: a SET-but-rejected STT token silently produced
transcript-less meetings). A capability may declare a **probe** (`$defs/Probe`), one cheap
verification, two kinds:

- **`http`** — one authenticated request (`Authorization: Bearer <auth_key>`) to
  `<url_key><path>`. An `unauthorized_statuses` answer (default 401/403) or a network
  failure/timeout ⇒ **misconfigured** with a reason; ANY other status (400/404/405/…) proves the
  endpoint is reachable and the credential accepted ⇒ ok.
- **`file`** — a credentials file as visible to THIS service: the `path_key`'s own path, then any
  `fallback_paths` (in-container mirror mounts of a docker-HOST path — see the runtime's
  `HOST_CLAUDE_CREDENTIALS`). A regular, readable, non-empty JSON file ⇒ ok; a DIRECTORY (docker's
  bind-mount of a MISSING host path — the 'Not logged in' signature), unreadable, or non-JSON ⇒
  **misconfigured**. Skipped (not failed) when `path_key` is unset and the capability is satisfied
  by another key (mode=any).

**Cadence:** once at boot (`preflight()` — logged, never boot-blocking) and lazily on `/health`
when the cached verdict is older than `ttl_s` (default 60s; `timeout_s` default 2s stays under the
deploy healthcheck timeouts). Probe failure demotes the `/health` row to `misconfigured`; it never
flips the service's own `status`, and the request-path oracle (`capability_state`) stays pure —
no probe I/O rides a spawn.

## The preflight (boot)

`preflight.py` here is the **canonical copy**; each service vendors it verbatim as
`config_preflight.py` next to its declaration (`gate:config-contract` enforces byte-equality — the
services share the contract, not a package dependency, P2). At boot the service calls
`preflight()`: missing required-explicit keys ⇒ one `ConfigError` naming them all; capability
states (+ probe verdicts) are logged. At runtime `capability_state("stt")` /
`missing_capability_keys("stt")` drive the typed gate errors, and `capability_health()` feeds the
`/health` rows. Env-level state is computed from the env **at call time** (no boot snapshot).

## gate:config-contract (`pnpm gate:config-contract`)

For every adopted service (meeting-api · runtime · agent-api — the three planes):

1. the declaration conforms to `config.schema.json` (this dir's `validate.mjs --file`);
2. the vendored `config_preflight.py` is byte-identical to the canonical `preflight.py`;
3. **declaration → surfaces:** every key appears in each deploy surface its `targets` names
   (compose: the service's `environment:` block or `.env.example` when `env_file`-fed; helm: a
   `- name: KEY` in the service's deployment template; lite: the service's supervisord
   `environment=` line or an `entrypoint.sh` export);
4. **surfaces → declaration:** every key a surface sets on the service is declared (or listed in
   `surface_only` with a reason) — a tight infra allowlist (`PYTHONPATH` etc.) is documented in the
   gate;
5. **undeclared-read scan:** every literal `os.getenv` / `os.environ` read in the service's source
   names a declared key.

## Adopters

| service | declaration | boot hook |
|---|---|---|
| meeting-api | `core/meetings/services/meeting-api/src/meeting_api/config.v1.json` | `__main__._require_config` → `preflight()` |
| runtime | `core/runtime/src/runtime_kernel/config.v1.json` | `__main__.build_production_app` → `preflight()` |
| agent-api | `core/agent/control_plane/config.v1.json` | `api._build_production_app` → `preflight()` |

Other services adopt by shipping a declaration + the vendored preflight and registering in the
gate's `CONFIG_ADOPTED` table.

**Depends on:** nothing (a leaf contract; the preflight is stdlib-only). Consumed by: the three
planes' boot paths, `scripts/gates.mjs` (`gate:config-contract`), and the deploy surfaces it keeps
honest.
