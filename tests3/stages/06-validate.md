# Stage: validate

| field        | value                                                                   |
|--------------|-------------------------------------------------------------------------|
| Actor        | mechanical                                                              |
| Objective    | Three-phase validate (plan / execute / resolve); emit Gate verdict.     |
| Inputs       | `scope.yaml` + `features/*/dods.yaml` + `registry.yaml`                 |
| Outputs      | `.state/reports/<mode>/*.json` + `reports/release-<tag>.md` + AUTO-DOD  |

## Steps (Makefile: `release-validate`)
1. `lib/stage.py assert-is deploy`.
2. **PLAN** — `lib/run` builds the execution graph: filter `registry.yaml` by scope × modes, group by `state:`, order stateful serial, stateless parallel.
3. **EXECUTE** — run the graph; each entry emits `.state/reports/<mode>/<test>.json` via `test_begin/step_*/test_end` helpers (`lib/common.sh`).
4. **RESOLVE** — `lib/aggregate.py` loads sidecar DoDs + reports, evaluates every DoD, computes per-feature confidence, writes `reports/release-<tag>.md` + updates feature README AUTO-DOD blocks.
5. Gate verdict:
   - **green** → `lib/stage.py enter human`.
   - **red**   → `lib/stage.py enter triage`.

## Authoritative-only — validate is the slow path

Validate is the AUTHORITATIVE cross-mode gate, not the dev loop. Each
invocation:
1. Resets the entire stack on every scope mode (release-reset).
2. Rebuilds + pushes ALL images that have any code change since last
   release-build (8+ images: bot, lite, runtime-api, api-gateway,
   admin-api, mcp, meeting-api, dashboard, tts-service).
3. Runs the full registry × scope matrix on lite + compose + helm.

Wall-clock cost: ~20-30 min per invocation. Justified for the gate,
NOT for dev iteration.

**On red → triage → develop, do NOT re-fire `release-validate` for
each iteration of the fix.** The hot-iterate dev loop in
`tests3/stages/03-develop.md` (Make target: `make hot-iterate SERVICE=<n>
[SCOPE=<s>]`) rebuilds ONE image and runs scope-filtered tests on
compose only — typical inner-loop time <5 min.

When the hot-iterate cycle converges (compose tests pass for the fix),
THEN run the full authoritative `release-deploy` + `release-validate`
ONCE to re-enter human stage. That single authoritative run is fresh,
cross-mode, and gate-grade.

## Exit
Gate verdict recorded; stage transitioned to `human` (green) or `triage` (red).

## May NOT
- Edit code.
- Change infra.
- Re-try failed tests without root-cause investigation ("flake retry" is forbidden — see triage).
- **Pass the gate while `BOT_NO_UNJUSTIFIED_FALLBACKS` reports new fallback patterns added in this cycle's diff** without a corresponding `#NNN` issue ref on the same line. The check surfaces matches as warnings on existing code (false-positive risk is high) but FAILS the gate when a NEW fallback is introduced. Captured 2026-05-01 after the v0.10.5.2 cycle shipped `__vexaRecordedChunks` chunk-buffer leak as a "fallback for shutdown-flush" that crashed customer meetings at 24 min. The develop stage's text rule against fallbacks is primary enforcement; this check is the validate-stage backstop.
- **Pass the gate while `RELEASE_DOCS_NO_PII` finds customer PII in `tests3/releases/<id>/`**. Any real-looking email not `@redacted` or common-name pattern in release artifacts fails the gate. Captured 2026-05-01 after the v0.10.5.2 cycle leaked 5 customer names + emails + GH/Discord handles into the OSS public repo. Anonymize at write-time (`customer-A`, `customer-B`, ...); never copy real names from prod telemetry into release docs.

## Next
`human` (on green) | `triage` (on red).
