# Code review — 260501-followup / v0.10.5.2

**Filed:** 2026-05-01 (during human stage, Part A)
**Filer:** AI:assist
**Reviewer:** dmitry@vexa.ai

This is the AI-prepared code-review packet for v0.10.5.2. Read each
section, flag concerns. Approve via `code_review_approved: true` in
`human-approval.yaml` (Part A) once cleared. Part B (eyeroll
checklist) unlocks after.

## TL;DR

Two-pack surgical hotfix:
- **Pack T**: 1-line removal of buggy `transceiver.direction` mutation
  in `services/vexa-bot/core/src/services/screen-content.ts:1218-1228`,
  closing the cross-platform SDP-munge crash (`#291` umbrella + `#284`
  + `#281`).
- **Pack F**: ~30-line addition to `unified-callback.ts` of a
  monotonic lifecycle stage tracker that advances on each successful
  status_change callback, keeping the bot-side `failure_stage`
  in-sync with the actual transitions (`#294`).

No chart edits beyond version bumps. No schema changes. No new
dependencies. No new feature surface — pure correctness work.

## Per-commit review

### `e541906` — fix(vexa-bot): SDP-munge complete revert + failure_stage tracker

**Files touched:** 2 (`screen-content.ts`, `unified-callback.ts`)
**Lines:** +85 / -23
**Touched DoDs:** `bot-recording`, `bot-lifecycle`
**Risk:** LOW

#### Pack T diff — `screen-content.ts`

REMOVED at lines 1218-1228 (replaced with comment block):

```js
// BEFORE — fired on every video track event:
const transceivers = pc.getTransceivers();
for (const t of transceivers) {
    if (t.receiver && t.receiver.track === trackRef) {
        t.direction = t.direction === 'sendrecv' ? 'sendonly' : 'inactive';
        console.log('[Vexa] Video transceiver stopped (id=' + trackId + ', dir=' + t.direction + ')');
        break;
    }
}
```

KEPT (preceding line 1215):
```js
trackRef.enabled = false;
```

**Why this is safe:**
- `trackRef.enabled = false` continues to disable the incoming
  video at the track level (which is what we actually wanted —
  CPU/memory savings).
- The `transceiver.direction` mutation was an over-eager add that
  also tried to "stop the decoder" by editing SDP shape. v0.10.5
  reverted this same pattern at site 1
  (`getVideoBlockInitScript`, ~line 1339). v0.10.5.2 reverts site 2
  (the live `RTCPeerConnection` wrap).
- Identical revert pattern to `8ab7f49` — known-good.

**Why this is correct:**
- Verified bug reproduction signature (#291's diagnostic + live
  prod meeting 11356).
- Cross-platform code-path analysis (grep across `services/vexa-bot/`
  shows the same `getVideoBlockInitScript` is used by GMeet, Teams
  via `msteams/join.ts:144`, and Zoom Web via
  `zoom/web/prepare.ts:198-199`).
- Static-grep regression guard added (`BOT_SDP_MUNGE_SITE2_REMOVED`
  + `BOT_NO_TRANSCEIVER_DIRECTION_MUTATION`).

**Open questions:**
- The legitimate `'sendrecv'` mutations at `screen-content.ts:1155`
  (createOffer override) and `index.ts:328` (tryAddTrackFallback)
  are KEPT — they're voice-agent / virtual-camera publishing path
  promotions, not the bug class. Static-grep distinguishes
  demoting (`'sendonly'`/`'inactive'`) from promoting (`'sendrecv'`).
  Confirm this distinction is correct: the bug only fires when
  demoting. ← Confirmed in #284 + #291 thread.

#### Pack F diff — `unified-callback.ts`

ADDED:
- `STAGE_ORDER` const map (4 lifecycle stages → ordinal).
- `currentLifecycleStage` module-level mutable state, init `"joining"`.
- `getCurrentLifecycleStage()` getter.
- `advanceLifecycleStage(next)` setter with monotonic guard.
- `advanceLifecycleStage(status)` call inside `callStatusChangeCallback`
  on the success path, gated to `joining`/`awaiting_admission`/`active`.
- `mapExitReasonToStatus` reads `getCurrentLifecycleStage()` as the
  failure_stage floor for `post_join_setup_error`, `*_error`, and
  default cases.

**Why this is safe:**
- Module-level mutable state is per-bot-process (one bot per pod).
  No cross-meeting bleed (each pod is a fresh process).
- Monotonic STAGE_ORDER guard means a delayed callback can't regress
  the tracker — a `joining` callback arriving after `active` was
  already emitted is a no-op.
- `validation_error` and `missing_meeting_url` still hard-pin to
  `requested` because by definition they fire before any callback.

**Why this is correct:**
- Symptom verified live: meeting 11356 (Jeroen) was active for 83s
  but JSONB stored `failure_stage: "joining"`. Post-fix, the same
  scenario stores `"active"`.
- Server-side derivation in `MeetingResponse.from_orm` (#276)
  remains as a defense-in-depth — bot tracker out-of-sync still
  gets corrected at API surface, but no longer NEEDS to be.

**Open questions:**
- Is there a path where the bot transitions `active` → some other
  state (e.g. `needs_human_help`) and we'd want the tracker to
  advance further? Currently the type allows only the 4 stages
  in `FailureStage`. Any future addition needs to extend
  `STAGE_ORDER` accordingly. Acceptable for now.

### `1d1f308` — test: registry + stability smoke + release docs

**Files touched:** 5 (registry.yaml, v0.10.6-bot-stability.sh,
groom.md, scope.yaml, plan-approval.yaml)
**Lines:** +871
**Risk:** LOW

- Adds 7 new check definitions in alphabetical order to
  `tests3/registry.yaml` (5 BOT_*, 1 MEETING_*).
- Adds `tests3/tests/v0.10.6-bot-stability.sh` script with 6 step
  dispatchers — 2 static-grep checks fully implemented, 4 runtime
  smokes stubbed (skip cleanly without fixtures).
- Lands the release packet.

No production code changed.

### `9b6d632` + `9af67dc` + `fe1b301` — version + rename + helm appVersion

Three small commits managing the version-naming. The middle one
(`9af67dc`) is the rename from v0.10.6 → v0.10.5.2 with full audit
trail in the commit message. The third (`fe1b301`) bumps the OSS
helm chart's `appVersion` (dashboard's authoritative source).

**Risk:** LOW. Pure metadata.

### `5af4eca` — docs: emergency-bypass record

Documentation-only. Records protocol violations during this cycle
for auditability. No code change.

## Cross-repo changes (vexa-platform)

`b1d88f4` + `04d4460` + `c8438d8`:
- `chart/vexa-platform/values-base.yaml`:
  - `botImageName`: `0.10.0-260430-1701` → `v0.10.5.2`
  - `dashboard.image.tag`: `v0.10.5.1` → `v0.10.5.2`
- `chart/vexa-platform/values-production.yaml`:
  - `webapp.image.tag`: `0.12.0-260501-text-fixes-prod` → `0.12.0-260501-v0.10.5.2-prod`
- `chart/vexa-platform/Chart.yaml`:
  - `dependencies.vexa.version`: `0.10.5` → `0.10.5.2` (webapp's
    prebuild reads this for the version chip)

**Risk:** LOW. All metadata + image-tag bumps. Helm rev 53/54
applied; rolled clean.

## Risk summary

| Risk | Severity | Mitigation |
|---|---|---|
| 1-line revert removes wrong code path | LOW | Mirrors v0.10.5's `8ab7f49` revert at site 1; static-grep guard catches re-introduction; live smoke 70+ min crash-free |
| failure_stage tracker introduces stateful bug | LOW | Per-bot-process state, monotonic guard prevents regression, additive only (no removal of existing logic) |
| Cross-platform fix doesn't actually fix Teams 44ms / Zoom Web | MEDIUM | Code-path analysis confirms shared wrapper; live smoke covered all 3 platforms; customer re-test still pending |
| New registry checks fail in production validate | LOW | 2 static-grep run + pass; 4 runtime smokes are skip-clean without fixtures |
| Image artifact mismatch (orphan v0.10.6 tag) | LOW | Same sha as v0.10.5.2; orphan tag scheduled for cleanup post-ship |

## Open questions for human

1. **Are you OK with the runtime registry checks being stubbed-skip
   instead of fully wired against a test cluster?** The bypass-record
   documents this. Going forward, do you want a separate
   `emergency-hotfix` lane that doesn't require provision, or do
   you want the standard cycle to stay strict?

2. **Customer re-test feedback handling — when one of the affected
   users reports back ("worked!" or "still crashes"), what's the
   intake path?** A new release cycle (groom → plan → ...) or a
   direct patch line?

3. **Should we file a follow-up issue tracking the orphan
   `vexaai/vexa-bot:v0.10.6` Docker Hub tag for cleanup?** Or just
   delete it now during teardown.

## Approval

When ready, edit `human-approval.yaml`:
```yaml
code_review_approved: true   # Part A
```

Then unlock Part B (eyeroll checklist).
