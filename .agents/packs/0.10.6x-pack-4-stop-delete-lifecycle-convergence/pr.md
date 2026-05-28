# Pack PR: [Pack] PACK 4 - Stop/Delete Lifecycle Convergence

Pack epic: https://github.com/Vexa-ai/vexa/issues/359
Pack id: `0.10.6x-pack-4-stop-delete-lifecycle-convergence`
Release: `0.10.6.x replay`
Base branch: `v0.10.6^{}`
Integration branch: `codex/release-0.10.6x-pack-integration`
Evidence: `.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/`

## Outcomes

CEO: Stopping a meeting should reliably end the session instead of leaving stuck runtime or bot resources.

CTO: Runtime callbacks, internal secret preservation, and lifecycle metadata converge stop/delete into terminal meeting state without stale sessions.

User: When a bot is stopped or deleted, the meeting reaches a final state and resources are cleaned up.

## Scope

- #313
- lifecycle/callback portions of #331/#347

## Out of scope

- Current uncommitted product changes in the local worktree are excluded from this pack.
- Dead release evidence folders are excluded as source truth.
- Deprecated harness evidence is excluded; reusable checks must live in product tests or dedicated skills.
- The removed realtime transcript regression pack is not replay scope here.

## Blast radius

Runtime API lifecycle, bot callback payloads, meeting status convergence, cleanup sweeps, stale browser/bot sessions.

## Validation

Synthetic: pass
- `runtime-api`: 32 passed in `tests/test_api.py tests/test_lifecycle.py tests/test_state.py`
- `meeting-api`: 83 passed in `tests/test_callbacks.py tests/test_meetings.py tests/test_sweeps_stopping.py`
- `vexa-bot` callback/header focused checks passed.

Compose: pass
- Isolated project `vexa_0-10-6x-pack-4-stop-delete-lifecycle-convergence_compose`
- Rebuilt pack-branch `meeting-api` and `runtime-api` images under `0.10.6-pack4-260523-1521`.
- Browser session delete proof reached `completed`; Runtime API returned 404 for the deleted container.
- `make all` service health passed for API Gateway/Admin/Dashboard, then failed transcription smoke on local placeholder token HTTP 401.

Lite: blocked by isolation
- Official Lite `make lite` path uses fixed container names `vexa-lite`/`vexa-postgres`, host networking, and fixed ports 3000/8056/8057.
- Those names/ports are already occupied by unrelated local Lite lanes. Running it would violate pack isolation.

Live/human: not required; this pack was validated with synthetic and local Compose lifecycle checks.

Hardenloop: no release blockers, incomplete local scanner coverage
- Final run output: `.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run-final`
- `bandit` ran; several external scanners were missing/unavailable locally.

Known caveat:
- Full `services/vexa-bot/core` build is blocked by pre-existing TypeScript issues in `src/index.ts` and local Playwright type resolution. Pack callback/header files pass focused checks.

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

- [ ] Pack branch starts from `v0.10.6^{}`.
- [ ] Only this pack's committed reuse hunks are replayed.
- [ ] Synthetic checks pass before live/human checks.
- [ ] Compose gate is passed or explicitly marked not required in PR evidence.
- [ ] Lite gate is passed or explicitly marked not required in PR evidence.
- [ ] Hardenloop is run for the pack.
- [ ] PR body links this epic and evidence root.
- [ ] Reviewer can map each reused hunk back to the commit list above.
