# Pack PR: [Pack] PACK 7 - Release Identity And Packaging Hardening

Pack epic: https://github.com/Vexa-ai/vexa/issues/362
Pack id: `0.10.6x-pack-7-release-identity-packaging-hardening`
Release: `0.10.6.x replay`
Base branch: `v0.10.6^{}`
Integration branch: `codex/release-0.10.6x-pack-integration`
Evidence: `.agents/packs/0.10.6x-pack-7-release-identity-packaging-hardening/`

## Outcomes

CEO: Operators can tell exactly what candidate is running and ship or roll back with confidence.

CTO: Source, Docker image, Helm chart, dashboard version, CI, and hardening metadata identify the same candidate and avoid release-only drift.

User: Self-hosted and production operators see coherent version/package behavior instead of mixed source/runtime identity.

## Scope

- #349
- release/provenance/hardening scope from #348/#331

## Out of scope

- Current uncommitted product changes in the local worktree are excluded from this pack.
- Dead release evidence folders are excluded as source truth.
- Deprecated harness evidence is excluded; reusable checks must live in product tests or dedicated skills.
- The removed realtime transcript regression pack is not replay scope here.

## Blast radius

Release provenance, Docker images, Helm chart SemVer/appVersion, dashboard version display, dependency floors, security headers, CI workflows.

## Validation

Synthetic: pass. Covered source identity proof, dashboard release generator/assertion proof, meeting-api security header unit tests, Helm lint/template, and npm lock consistency dry-runs.

Compose: pass. `docker compose config` rendered `IMAGE_TAG=0.10.6.2.1`, `VEXA_VERSION=0.10.6.2.1`, dashboard port `44460`, and gateway port `44461`.

Lite: source-level pass. Runtime Lite image validation is deferred until product-pack stitching per the pack epic.

Live/human: not required for this pack directly.

Hardenloop: ran with `--fix none`; release blockers `0`; decision `incomplete_coverage` because several optional local scanners are not installed. Private advisory/raw finding payloads were omitted from committed evidence; see `hardenloop/state.md`.

## Evidence checklist

- [x] `pack.json`
- [x] `runtime.json`
- [x] `ops/ops.jsonl`
- [x] `tests`
- [x] `compose`
- [x] `lite`
- [x] `hardenloop`
- [x] `review.md`
- [x] `pr.md`

## Stitching notes

- Shared files must be merged by hunk and reviewed against adjacent pack edits.
- If stitching exposes a behavior bug, route it back to the responsible pack instead of hiding a stitch-time code change.
- This epic is not a release sign-off; it is the delivery contract for one independently reviewable pack.

## Review checklist

- [x] Pack branch starts from `v0.10.6^{}`.
- [x] Only this pack's committed reuse hunks are replayed.
- [x] Synthetic checks pass before live/human checks.
- [x] Compose gate is passed or explicitly marked not required in PR evidence.
- [x] Lite gate is passed or explicitly marked not required in PR evidence.
- [x] Hardenloop is run for the pack.
- [x] PR body links this epic and evidence root.
- [x] Reviewer can map each reused hunk back to the commit list above.
