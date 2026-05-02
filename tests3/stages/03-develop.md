# Stage: develop

| field        | value                                                                          |
|--------------|--------------------------------------------------------------------------------|
| Actor        | human (code) + AI (assist)                                                     |
| Objective    | Write code + tests + `dods.yaml` entries implementing the approved scope.      |
| Inputs       | `scope.yaml` + (if entered from `triage`) `triage-log.md`                      |
| Outputs      | Commits on `dev` branch                                                        |

## Steps
1. `lib/stage.py assert-is develop` — halt if wrong stage.
2. For each scope issue, implement code + test + (if needed) add / update DoD in feature sidecar.
3. Every new check ID referenced by scope `proves[]` must exist in `registry.yaml` before moving on.
4. Commit. Trailer format: `release: <id> · stage: develop`.

## Exit
All scope issues have commits; every new `proves[]` check id exists in `registry.yaml`.

## May NOT
- Touch infra (`provision` / `deploy`).
- Run validate.
- Advance stage without all scope commits present.
- **No fallbacks unless explicitly decided** with a written decision in scope.yaml. Any code that runs on the failure side of an `if (!ok)` / `try-catch` / "in case the primary path fails" comment, or any "buffer kept around just in case" / "default value used when X is missing" / "fallback for shutdown-flush" pattern, requires a corresponding `scope.yaml` `proves[]` entry naming the fallback explicitly + a `#NNN` GH issue ref on the same line as the fallback in the source. Captured 2026-05-01 after the v0.10.5.2 cycle's `__vexaRecordedChunks` "shutdown-flush fallback" produced an unbounded memory leak that crashed real customer meetings at 24 min. Pattern repeats: `default-secret-change-me`, auth-cookie schema-fallback, env-example fallbacks, server-side `failure_stage` derivation as a fallback for the bot tracker. Each "for safety," each producing a different bug class. If the fallback isn't worth filing an issue for, it isn't worth shipping.
- **Leak customer PII into release artifacts.** Any file under `tests3/releases/` (groom.md, scope.yaml, validate-report.md, code-review.md, human-checklist.md, etc.) MUST use anonymized customer refs (e.g. `customer-A`, `customer-B`). Real names + emails belong in private CRM / vexa-platform repo only. Captured 2026-05-01 after the v0.10.5.2 cycle leaked 5 customer names + 5 emails + 3 GH/Discord handles into the OSS public repo. The `RELEASE_DOCS_NO_PII` registry check (Pack P, v0.10.5.3) flags violations at validate-stage gate.

## Hot-iterate dev loop (when entered from `triage`)

When develop is entered from `triage` (i.e. fixing a single failure that
came back red from validate), the AUTHORITATIVE path is full
`release-deploy` + `release-validate` against fresh state on all 3 modes.
But that's slow (~20-30 min per cycle) when the fix is one or two lines.

Use the **hot-iterate** dev loop to converge fast on compose first, THEN
do the full authoritative cycle once. Total time saved: ~5-10× on small fixes.

```
Slow path (don't iterate this way):
  edit → release-deploy (rebuild ALL 8+ images, push, redeploy lite + compose + helm) → release-validate (full matrix on 3 modes) → if red, repeat

Fast path:
  edit → hot-iterate.sh <service>  (rebuilds ONE image, pushes, recreates only that service on compose, runs scope-filtered tests on compose only)
  → if red, edit again
  → if green, ONE final authoritative release-deploy + release-validate to confirm cross-mode and re-enter human
```

Tooling:
- `bash tests3/lib/hot-iterate.sh <service> [<scope-yaml>]` —
  builds + pushes ONLY that service's image, recreates it on compose VM
  via `docker compose up -d --no-deps --force-recreate <service>`, then
  runs scope-filtered tests on compose only.
- Service names: `vexa-bot`, `dashboard`, `meeting-api`, `runtime-api`,
  `admin-api`, `api-gateway`, `mcp`, `tts-service`, `vexa-lite`.
- Or via Make: `make hot-iterate SERVICE=<name> [SCOPE=<scope-yaml>]`.

When to STOP iterating hot and switch to authoritative validation:
- Compose tests are passing for the fix.
- You're about to push for `human` stage entry.
- The full release-deploy + release-validate matrix MUST be the
  signal that exits `develop`. Per the validate-stage contract, the gate
  is "fresh authoritative matrix on all 3 modes." Hot-iterate is a
  dev-loop convenience, NOT a gate.

## Next
`provision` — if entered from `plan` (first-time infra).
`deploy` — if entered from `triage` (infra already up; just push the fix).

## AI operating context
You are in `develop`. You help the human write code + tests + DoDs per the scope. You may edit files under `services/`, `features/`, `tests3/tests/`, `tests3/registry.yaml`. You may NOT run validate, touch infra, or advance stage yourself. Every code change aligns with a scope issue or the triage-log; refuse ad-hoc work: "I am in develop; show me which scope issue / triage item this serves."
