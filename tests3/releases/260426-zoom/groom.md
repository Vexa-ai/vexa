# Groom — 260426 (zoom delivery)

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| release_id   | `260426` *(slug assigned by `plan` — proposed: `zoom`)*                |
| stage        | `groom`                                                                |
| entered_at   | `2026-04-26T07:30:55Z`                                                 |
| actor        | `AI:groom`                                                             |
| predecessor  | `idle` (prior release `260422-release-plumbing`, shipped 2026-04-22)   |
| theme (AI)   | *"Promote Zoom (Playwright/Web) from operator-private env-var dispatch to first-class API platform with DoD coverage."* |

---

## Scope, stated plainly

The Zoom Web (browser-automation) **code path already exists in `main`** as
of PR #181 (commit `33ff771 feat: Zoom web client + video recording`), under
`services/vexa-bot/core/src/platforms/zoom/web/*` (~1,133 LOC: join,
admission, prepare, recording, removal, leave, selectors). It runs today
**only when the meeting-api operator sets `ZOOM_WEB=true` in their env** —
at which point any `POST /bots {platform: "zoom"}` becomes a Web-flavoured
join. SDK is the default path otherwise.

That dispatch shape has three problems for delivery:

1. **No public API distinction.** External callers cannot pick "Web" vs
   "SDK" per request. Whichever the operator wired up server-side wins.
   The hosted product cannot offer both. Self-hosters cannot mix.
2. **No pre-flight contract.** `POST /bots {platform: "zoom"}` returns
   `201` regardless of whether the chosen path's prerequisites (browser
   runtime for Web; SDK artifacts for native — see #128) are actually
   present. Failures surface as silent transitions to `failed`.
3. **No DoDs.** `tests3/features/realtime-transcription/` has no
   `zoom_web/` folder. Releases cannot claim "Zoom Web works" because
   no gated check enforces it. Issue #254's Phase 3 calls this out
   explicitly.

This release lifts Zoom Web out of the env-var hack and makes it a
first-class platform that hosted-Vexa can offer alongside SDK, with
the regression-and-pre-flight scaffolding the other platforms have.

The companion **Zoom SDK** track (#253, the abandoned-and-now-recovered
`origin/release/260422-zoom-sdk` branch with 12 unmerged commits) is
**explicitly out of scope for this cycle.** That branch's enum-split
+ pre-flight scaffolding is relevant prior art and may inform Pack A's
shape, but shipping it is its own cycle.

### Why this cycle, why now

- **PR #181 has been in `main` since the previous cycle.** No DoD
  exercises it. Whether the code actually works end-to-end against a
  live Zoom meeting on current `main` is — for the gate — **unknown**.
  Every day this stays un-DoD'd is a day a refactor can silently break
  it.
- **A 5+-week-old open contributor PR (#179, @jbschooley) is sitting
  unmerged.** It's superseded by #181 (different author, same problem
  domain). Closing it cleanly with thanks is overdue and falls
  naturally inside this cycle.
- **The hosted product roadmap wants Zoom.** Without first-class
  API-level platform selection, hosted-Vexa cannot offer Zoom-Web bots
  even though the code is ready.

---

## Signal sources scanned

| source                                                              | count | notes                                                            |
|---------------------------------------------------------------------|------:|------------------------------------------------------------------|
| `gh issue list --state open`                                        |    74 | scanned in full; only #254 / #253 / #150 / #128 in zoom-family   |
| Issue [#254](https://github.com/Vexa-ai/vexa/issues/254) (epic)     |     1 | full body — phases, scope, deps, open questions; this is the canonical scope |
| `gh pr view 179`                                                    |     1 | OPEN, CONFLICTING, last update 2026-04-25 13:46Z, +2168/-56, no reviews |
| `git log -- services/vexa-bot/core/src/platforms/zoom/`             |     4 | latest: `33ff771 feat: Zoom web client + video recording (PR #181)` |
| `git log origin/main..origin/release/260422-zoom-sdk`               |    12 | abandoned branch; relevant prior art for Pack A enum split       |
| `services/meeting-api/meeting_api/schemas.py:192-200` (Platform enum) | — | values: `google_meet`, `zoom`, `teams`, `browser_session`. NO `zoom_web` / `zoom_sdk`. |
| `services/meeting-api/meeting_api/meetings.py:1029-1037` (dispatch) | — | env-var-driven: `os.getenv("ZOOM_WEB","")=="true"` flips Web vs SDK |
| `tests3/features/realtime-transcription/` (DoD home)                | — | no `zoom_web/` subfolder; existing folders cover gmeet+teams only |
| Discord                                                             |     — | no in-repo fetcher (README §4.2 still future work); skipped      |

---

## Packs — candidates for this cycle

Ordered by what gates "Zoom Web is shippable as a first-class platform."

### Pack A — Platform-enum upgrade: `zoom` IS Web; `zoom_sdk` is SDK  (**recommended: YES, P0**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) Phase 2 + Phase 3 implicit prerequisite; user direction 2026-04-26 (*"zoom will be for web, zoom_sdk for sdk"*).
- **symptom**: Hosted Vexa and self-hosters cannot offer both Zoom paths simultaneously. The choice is server-wide, set via `ZOOM_WEB=true` env on meeting-api. `POST /bots {platform: "zoom"}` is ambiguous from the API perspective — same input, different bot behaviour depending on operator config.
- **severity**: **P0 / blocks delivery.** Without this, "shipping Zoom Web" means "shipping Zoom Web *and unshipping Zoom SDK on the same deployment*."
- **scope shape (groom view; plan finalises)**:
  - Extend `Platform` enum at `services/meeting-api/meeting_api/schemas.py:192-200`:
    - Add `ZOOM_SDK = "zoom_sdk"`.
    - **`ZOOM = "zoom"` IS the Web path.** No alias indirection, no resolution chain. The existing enum value's *meaning* changes: `platform=zoom` always dispatches Web. SDK callers must request `platform=zoom_sdk` explicitly.
  - **Backward-compat note for plan to call out**: any existing caller passing `platform=zoom` while the operator had `ZOOM_WEB=true` set keeps working unchanged (still Web). Any existing caller passing `platform=zoom` while the operator did NOT set `ZOOM_WEB` was getting the SDK path; under the new dispatch they get Web instead. **This is a breaking change for SDK-default operators.** Plan documents in the scope's `breaking_changes` section + ship-stage release notes; there's no shim.
  - Replace the env-var dispatch at `meetings.py:1029-1037` with platform-enum dispatch:
    - `req.platform == ZOOM` → set `env_vars["ZOOM_WEB"]="true"` for the bot (Web is the only Zoom path under this enum value).
    - `req.platform == ZOOM_SDK` → set `ZOOM_CLIENT_ID`/`ZOOM_CLIENT_SECRET`.
  - Update `bot_name` mapping (`schemas.py:208-212`) so `ZOOM_SDK` resolves to `bot_name=zoom` on the bot side (the bot is already a single image whose internal dispatch happens via env).
  - Update meeting-id/URL parsers (`schemas.py:228, 266, 587, 659`) to accept `zoom_sdk` for the same Zoom URL shape (bare meeting-ID or `zoom.us/j/...`).
  - **Operator-side env var `ZOOM_WEB=true` is retired.** It was never a public contract — only an internal toggle. The new dispatch makes it dead code; remove it cleanly.
- **estimated scope**: ~5 edits in `schemas.py` + `meetings.py`. **~0.5 day** including unit-test coverage of the dispatch (no resolution chain, no user-preference, no settings endpoint, no migration).
- **repro confidence**: HIGH — the enum surface is small and the dispatch site is one if-block.
- **owner feature(s)**: `infrastructure` (API surface) + a new feature folder `realtime-transcription/zoom/` (Pack C) anchored to the Web path that `platform=zoom` now routes.
- **architectural note**: chose to make SDK a *separate enum value* rather than overload `platform=zoom` with a discriminator field, because the bot code already lives in disjoint subdirectories (`zoom/web/`, `zoom/native/`) and the deployment surfaces (Web needs Playwright; SDK needs `zoom_sdk_wrapper.node`) are non-overlapping. SDK becomes opt-in by name, not by env-var convention.

### Pack B — End-to-end validation on `main`  (**recommended: YES, P0**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) Phase 2 ("validate end-to-end on a real Zoom account").
- **symptom**: PR #181 landed the Web code on 2026-MM-DD but no record exists of a successful end-to-end run on current `main`. Whisper-side or audio-path regressions since merge could have broken it silently.
- **severity**: **P0 / unverified working code.** Pack A is meaningless if the underlying Web flow doesn't actually join + transcribe.
- **scope shape**:
  - **B.1 — manual smoke**: spin up the bot against a live test Zoom meeting, walk through `join → admission → recording → leave`, capture logs. **Required before plan signs off** so DoDs in Pack C are anchored to verified behaviour, not aspiration.
  - **B.2 — capture failure modes**: anti-automation (Zoom's bot-detection), audio-path issues (#251's territory), admission timing. Each becomes its own follow-on issue if surfaced; this cycle does NOT commit to fixing whatever Phase 1 finds — only to *finding* it.
  - **B.3 — automated smoke** (DoD-shaped): a tier-meeting / smoke-contract test that runs the Web path against a captured Zoom HAR or a kept-alive test meeting. Plan picks the test mode (real meeting in a tier vs. recorded fixture). Mirrors `tests3/.../tier-meeting/` shape used for gmeet+teams.
- **estimated scope**: B.1 ~0.5 day. B.2 is open-ended; cap at 0.5 day + log-only findings if not nailed. B.3 ~0.5 day. **~1.5 days total.**
- **repro confidence**: MEDIUM — this is exploratory by design. The code compiles and was code-reviewed at PR-time; we don't know what's broken until we run it.
- **owner feature(s)**: new `realtime-transcription/zoom_web/` (Pack C is its DoD-authoring half).

### Pack C — DoD authorship under `realtime-transcription/zoom/`  (**recommended: YES, P0**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) Phase 3 ("Author under `features/realtime-transcription/zoom_web/dods.yaml`" — folder name updated to match the new API enum: `platform=zoom` IS Web, so the feature folder is `zoom/`).
- **symptom**: zero gated checks for the Zoom (Web) path. The Registry will rubber-stamp any release that breaks it as long as gmeet+teams stay green.
- **severity**: P0 — the gate is the regression contract.
- **scope shape**:
  - Create `tests3/features/realtime-transcription/zoom/dods.yaml` (and any sibling structure the existing gmeet/teams folders have — README.md, fixtures/). When SDK lands in a follow-on cycle (#253), it gets its own peer folder `realtime-transcription/zoom_sdk/`.
  - Initial DoDs (mirroring #254 Phase 3, anchored to the API value `platform=zoom`):
    - `bot_joins_zoom` — `POST /bots {platform: "zoom", ...}` with valid prereqs reaches `active` state
    - `transcribe_zoom` — segments emitted from a Zoom (Web) meeting (anchored to Pack B's smoke fixture, whichever shape plan picks)
    - `pre_flight_rejects_unmet_prereqs` — `POST /bots {platform: "zoom"}` returns 4xx with structured error when Playwright runtime / browser binaries are missing (Pack D delivers the runtime check; this DoD asserts the API contract)
  - Pair each DoD with a Registry check (`script` or `tier-meeting` mode, plan picks per DoD). Echo the gmeet/teams shapes — no new evidence types.
- **estimated scope**: ~3 DoDs × scaffolding + 3 Registry checks. **~0.5-1 day** depending on whether the smoke fixture lives in `tests3/fixtures/` or runs live.
- **repro confidence**: HIGH for the YAML-authoring half (mechanical); MEDIUM for the check shapes (depends on what Pack B finds is feasible to assert).
- **owner feature(s)**: `realtime-transcription/zoom/` (new feature folder).
- **open question for plan**: should the `_template/` shape under `tests3/features/` be cloned, or do gmeet+teams already constitute the canonical shape? Audit at plan time.

### Pack D — Pre-flight check for `platform=zoom`  (**recommended: YES, MED — cheap once A lands**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) Phase 3 + analog of issue [#128](https://github.com/Vexa-ai/vexa/issues/128) for the Web path.
- **symptom**: with Pack A landed, `POST /bots {platform: "zoom"}` will succeed even when the bot image has no Playwright runtime, no Chromium binaries, or no network reach to Zoom. Failure surfaces as `requested → joining → failed` minutes later, same UX bug #128 documents for SDK.
- **severity**: MED — feature works for correctly-provisioned operators; misleads incorrectly-provisioned ones. Worth shipping in the same cycle as A so the new platform doesn't inherit #128's anti-pattern.
- **scope shape**:
  - In `meetings.py` `POST /bots` handler, after `platform == ZOOM` dispatch (Web):
    - Check the `BotProfile` runtime image declares Playwright/Chromium (config-time check; can be a `BotProfile.capabilities` flag set in `runtimeProfiles.yaml`).
    - On failure, return `422` with structured body: `{code: "ZOOM_PREREQ_MISSING", message: ..., missing: [...]}` mirroring #128's spec.
  - Pair with a static check in the Registry (`grep`-mode on `runtimeProfiles.yaml` confirming the Web-capable profile is declared and tied to the `zoom` platform).
- **estimated scope**: **~0.5 day** including a unit test on the 422 path and a Registry check.
- **repro confidence**: HIGH — the analog #128 work for SDK on the abandoned branch (commit `eda2dd9 feat(meeting-api): pre-flight zoom_sdk creates → 503 on missing SDK creds (#128)`) is direct prior art.
- **owner feature(s)**: `realtime-transcription/zoom/` (Pack C's `pre_flight_rejects_unmet_prereqs` DoD asserts this at the API level).
- **open question for plan**: status code — `422` (validation error) per #128's spec, or `503` (per the SDK-side commit) given the pre-condition is server-side capability rather than request-validation? Plan picks; document in OpenAPI either way.

### Pack E — PR #179 close-out  (**recommended: YES, cheap, P3**)

- **source**: open PR [#179](https://github.com/Vexa-ai/vexa/pull/179) (jbschooley, 2026-03-11; CONFLICTING since 2026-04-25). Superseded by PR #181 (different author, same problem domain — Web client + Playwright).
- **symptom**: the public PR list shows a 5+-week-old open contributor PR. Closing window is past — leaving it open misrepresents project state and disrespects the contributor's time.
- **severity**: P3 / housekeeping. **MUST land in this cycle** since this is the cycle that ships the work that obsoletes the PR.
- **scope shape**:
  - Comment on #179 thanking @jbschooley, explaining that #181 took the same problem domain and shipped first, and pointing to the parts of `services/vexa-bot/core/src/platforms/zoom/web/` that were preserved or reshaped from their direction.
  - Close PR #179 (do not merge — it's now redundant against `main`).
  - If the contributor wants to follow up on Web-path work (anti-automation hardening, etc), file a fresh issue rather than reopening #179.
- **estimated scope**: ~30 minutes including the close-out comment.
- **owner feature(s)**: not Registry-tracked (process action).
- **note**: this is a `ship`-stage follow-up if it's unsafe to land before the new code merges. Plan can route it to ship's `follow-ups` section instead of develop. Either works; recommend ship-side close-out so the comment can point to the merged release.

### Pack F — Audio-capture conformance with #251 (**recommended: DEFER**)

- **source**: epic [#251](https://github.com/Vexa-ai/vexa/issues/251) ("Audio-capture investigation") + #254's open question about coordinating Phase 2 validation with #251.
- **symptom**: Zoom Web uses the same browser audio pipeline (`ScriptProcessor` family, `services/vexa-bot/core/src/services/audio.ts`) as gmeet+teams. Whatever zero-segment regression #251 is investigating affects Web identically. Pack B's smoke could surface the same failure mode that #251 is already chasing.
- **why DEFER**:
  - #251 is its own active investigation cycle. Bundling it would double the scope and tangle two distinct fix-paths.
  - Pack B's smoke MAY surface a Zoom-Web-only audio issue (different DOM event timing for track activation, distinct admission-to-audio-subscription glue). If so, that's a new zoom-web-specific issue to file and land in a follow-on, NOT a pull-in of #251.
  - If #251 lands a fix during this cycle (e.g. AudioWorklet migration), Web inherits it for free.
- **severity**: MED-LATENT — the failure mode exists today on every browser-based platform; it's not Zoom-Web-specific.
- **route**: keep as deferred. If Pack B.1 surfaces zero-segment behaviour on a Web smoke, file a fresh issue and decide at triage.
- **owner feature(s)**: `realtime-transcription` (cross-platform audio) — already #251's home.

### Pack G — Anti-automation hardening (**recommended: DEFER**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) open question 3.
- **symptom**: Zoom periodically tightens browser-automation detection. PR #181's Web client may or may not be flagged today; will eventually be flagged on some future Zoom update.
- **why DEFER**: this is a maintenance contract, not a one-shot fix. Decisions ("patch as detection updates ship" vs "switch to authenticated-user mode like gmeet's `authenticated:true` flow" — see #98) don't belong in a delivery cycle that's establishing the floor; they belong in a Zoom-Web-hardening cycle once we have a baseline.
- **route**: revisit after this cycle ships. May want a tracking issue ("Zoom Web bot-detection hardening — open question") instead of a backlog rot.

### Pack H — Hosted-Vexa enablement decision (**recommended: DEFER — business call, not engineering**)

- **source**: epic [#254](https://github.com/Vexa-ai/vexa/issues/254) open question 2 ("Is hosted Vexa willing to run Zoom Web bots?").
- **symptom**: even with all of A+B+C+D shipped, hosted-Vexa exposing Zoom requires (a) Zoom legal/ToS check on automated browser join, (b) ops capacity for the support load, (c) pricing/packaging decision.
- **why DEFER**: not an engineering scope question. Surface to the human as "this cycle makes hosted-Zoom *technically* possible; the *whether-to-offer* call is yours and platform-side."
- **route**: human picks at plan or at ship.

---

## Suggested cycle shapes — human picks

### Shape 1 — Full delivery  (**my recommendation**)

- Pack A (enum upgrade — P0)
- Pack B (smoke + automated DoD-anchor)
- Pack C (DoD authorship — `realtime-transcription/zoom_web/`)
- Pack D (pre-flight check)
- Pack E (PR #179 close-out — ship-stage follow-up)
- **DEFER** Pack F (audio path / #251) — separate epic
- **DEFER** Pack G (anti-automation hardening) — maintenance contract; revisit after ship
- **DEFER** Pack H (hosted-Vexa enablement) — business decision

Total: **~3 days develop** (A 0.5 + B 1.5 + C 0.5-1 + D 0.5) + validate + human. Fits one INNER-loop cycle. Closes #254 fully. Lands the public-API shape that hosted-Vexa needs to offer Zoom (Web). SDK opts in by name (`platform=zoom_sdk`) when a follow-on cycle (#253) ships its track. Files anything Pack B surfaces as fresh issues for a follow-on cycle (kept tight; doesn't compound scope).

### Shape 2 — Tight delivery  (**fallback if capacity is lower**)

- Pack A + Pack C + Pack D. Skip Pack B's manual smoke; rely on the DoD's automated check (Pack C's `bot_joins_zoom_web` + `transcribe_zoom_web`) as the only validation.

Total: **~1.5 days.** Risk: if Pack B's manual smoke would have caught an audio-path or admission-timing issue, the DoD-shaped automated test might either (a) miss it (less aggressive coverage) or (b) flake under it (worse for gate signal). Still better than no DoDs, but Shape 1's manual pass first is cheap insurance.

### Shape 3 — Minimum (enum-only)

- Pack A only. Lifts Web to first-class API; punts validation, DoDs, and pre-flight to a follow-on.

Total: **~0.5 day.** Not recommended — leaves the new platform un-DoD'd, which is the same drift state we entered this cycle to fix. Only sensible if a follow-on cycle is *immediately* scheduled to ship Packs B+C+D.

### Shape 4 — Wider Zoom delivery (Web + SDK together)

- Shape 1 + pull #253 (Zoom SDK recovery) into this cycle.

Total: **~6-7 days.** Not recommended — #253 is its own cycle (12 commits to rebase + Phase 2 SDK validation + Phase 3 SDK pre-flight + DoDs). The two tracks share enum-split prior art (Pack A) but have non-overlapping validation surfaces and separate deployment requirements. Bundling guarantees neither ships clean. **Better to land Web first, SDK in a back-to-back cycle.**

---

## Halt

`groom` stops here. `scope.yaml` is `plan`'s output.

### Human approval — 2026-04-26

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| approver     | dmitry@vexa.ai                                                         |
| approved_at  | 2026-04-26T07:30:55Z *(current session — user said "go")*              |
| signal       | User turn "go" after Pack-A simplification (zoom IS zoom web; zoom_sdk for SDK) and slug pick (`zoom`). |
| shape picked | **Shape 1 (recommended)** — Packs A + B + C + D + E                    |

Blanket approval interpreted as acceptance of every line in the checklist
below. Plan will echo each pack's `approved: true` into `plan-approval.yaml`
per the stage-02 contract.

- [x] Release slug: **`zoom`**. `make release-plan ID=260426-zoom SLUG=zoom` will realign `.current-stage.release_id` and rename the worktree.
- [x] Cycle shape: **Shape 1** (~3 days develop; Packs A+B+C+D+E).
- [x] **Pack A** — `Platform.ZOOM = "zoom"` IS Web; add `Platform.ZOOM_SDK = "zoom_sdk"` for SDK; replace env-var dispatch with enum dispatch; retire `ZOOM_WEB=true` env. **Breaking change** flagged for release notes. → `approved: true`.
- [x] **Pack B** — End-to-end smoke on `main` (B.1 manual, B.2 failure-mode capture, B.3 automated DoD-anchor test). → `approved: true`.
- [x] **Pack C** — Author DoDs under `tests3/features/realtime-transcription/zoom/` (`bot_joins_zoom`, `transcribe_zoom`, `pre_flight_rejects_unmet_prereqs`) + 3 Registry checks. → `approved: true`.
- [x] **Pack D** — Pre-flight 4xx for `platform=zoom` when prereqs missing; structured `ZOOM_PREREQ_MISSING` error. → `approved: true`.
- [x] **Pack E** — Close PR #179 with thanks (ship-stage follow-up, post-merge comment to @jbschooley). → `approved: true`.
- [x] **Pack F** deferred (audio-capture conformance with #251) → next groom cycle.
- [x] **Pack G** deferred (anti-automation hardening) → next groom cycle.
- [x] **Pack H** deferred (hosted-Vexa enablement business decision) → out of engineering scope.

### Waiting on human (deferred to plan, not blocking groom→plan)

- [ ] Confirm release scope is **Zoom (Web) only** (Web + SDK together = Shape 4, not recommended).
- [ ] Pick cycle shape: **Shape 1 (recommended)** / Shape 2 / Shape 3 / Shape 4.
- [x] **Pack A: `ZOOM = "zoom"` IS the Web path; `ZOOM_SDK = "zoom_sdk"` is the SDK path** (user direction 2026-04-26). No alias indirection, no per-user preference, no resolution chain. Operator-side `ZOOM_WEB=true` env var retired. **Breaking change** for operators who previously had `ZOOM_WEB` unset and relied on `platform=zoom` routing SDK — release notes must call this out.
- [ ] Confirm Pack E (PR #179 close-out) routes to **ship-stage follow-up** (post-merge comment) vs. **develop-stage close-out** (close before this cycle's PR opens).
- [ ] Confirm Pack F/G/H deferrals (audio #251, anti-automation, hosted-Vexa enablement).
- [x] **Slug: `zoom`** (user direction 2026-04-26 — *"zoom is zoom web"*). Plan renames worktree `vexa-260426` → `vexa-260426-zoom` and `release_id` → `260426-zoom`.

### Follow-ups deferred to plan (not blocking groom→plan)

- **Required reading for plan**: epic body of #254, abandoned-branch commit `387c624 feat(meeting-api): Platform enum adds ZOOM_SDK + ZOOM_WEB; alias ZOOM` for the prior-art shape, and the existing `realtime-transcription/{google_meet,teams}/dods.yaml` for the DoD template that Pack C clones.
- Plan's architectural-conformance audit (per `260421-prod-stabilize/groom.md` Iteration 3 pattern) should explicitly check:
  - Whether the `Platform` enum's `bot_name` mapping (`schemas.py:208-212`) needs a `ZOOM_SDK` entry or whether routing through `zoom` is sufficient (the bot doesn't care; only meeting-api does).
  - Whether `meetings.py:1029-1037` is the only dispatch site or if the env-var leak occurs elsewhere (e.g. `runtimeProfiles.yaml`, `runtime-api`).
  - Whether `BROWSER_SESSION` (the existing `Platform` value at `schemas.py:200` for headless interactive bots) should be confused with Zoom-Web. Spoiler: it shouldn't — different code path, different bot, different purpose.
- Plan must decide DoD evidence shape for Pack B.3 / Pack C: live tier-meeting (real Zoom account, real meeting, real cost) vs. captured-fixture replay (deterministic, but requires recorded HAR + audio fixtures). The gmeet+teams precedent leans live; cost/flake trade-off is plan's call.
- Plan's `registry_changes_approved` must include at minimum: 3-4 checks (one per Pack C DoD) + 1 grep-mode check for Pack A enum presence + 1 script-mode check for Pack D pre-flight 4xx behaviour.
- Cross-reference with #253 (Zoom SDK recovery): Pack A ships a chunk of #253's Phase 2 prerequisite (the enum split). If #253 ships in the next cycle, this cycle's Pack A removes one item from its scope. Track in this cycle's ship-stage follow-ups.

### Advancing (after human approval)

```bash
make release-plan ID=260426-zoom SLUG=zoom
```

(The `release-plan` target calls `stage.py enter plan --release 260426-zoom …` which both advances the stage AND realigns the worktree directory + `.current-stage.release_id`. See `tests3/stages/02-plan.md` step 2.)
