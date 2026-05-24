# PACK 1 Code Review

Status: completed with fix

## Findings

### Fixed: Dashboard returned JSON endpoint as media source

- Severity: blocker
- Files: `services/dashboard/src/lib/api.ts`, `services/dashboard/tests/test_recording_master_api.test.ts`
- Commit: `78c3e81`

The dashboard helper called `/recordings/{id}/master?type=audio`, confirmed the backend returned a media URL, but then returned `/api/vexa/recordings/{id}/master?type=audio&proxy=1` as the `<audio>` source. The dashboard proxy has no `proxy=1` media special case, so the browser would load JSON instead of audio/video.

Fix: return absolute presigned object-store URLs directly and route relative `/recordings/{id}/media/{media_id}/raw` paths through `/api/vexa...`. The focused test now covers both local raw proxy and object-store presigned URL behavior.

## Verification

- `tests/dashboard-recording-master-api-code-review-fix-rerun`: `3 passed`
- `tests/dashboard-touched-files-lint-code-review-fix-rerun`: passed
- `tests/dashboard-build-code-review-fix`: passed

## Residual Risks

- Hot human-in-the-loop Compose and Lite validation remains pending.
- Hardenloop remains `incomplete_coverage` due missing local scanners, with zero normalized release blockers.
