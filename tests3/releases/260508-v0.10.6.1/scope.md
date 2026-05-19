# Vexa v0.10.6.1

This is the canonical release document. Follows `tests3/release-doc-template.md`. Engineering detail in `scope.yaml`.

Signing attests: I have read this more than once, I understand what we are doing and why, and I confirm this is the balance I can deliver in this release.

**Deployment scope: lite + compose + helm.** Helm is rehearsed in this release; no helm-bound items defer.

---

## What you get


| Feature                                                                                                                                              | Significance                              | Blast radius if it regresses                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/speak` works in prod — TTS pod no longer crash-loops on first boot; bot honours the `provider` param                                               | **HIGH** (customer-A escalation)          | `/speak` undelivered in prod; named enterprise customer escalation reopens.                                                                                                                                                                                                                                                                                                                                           |
| Multichunk recordings play end-to-end — dashboard reads master, not chunk-0; ~73 historical recordings backfilled                                    | **HIGH** (data class)                     | Customers seeing only the first 30s of multi-chunk meetings return.                                                                                                                                                                                                                                                                                                                                                   |
| Recording integrity — `recording_finalizer` is the sole writer of `master_path` (Pack U.7 second race closed)                                        | **HIGH** (data class)                     | Silent DB↔filesystem disagreement on master file location returns.                                                                                                                                                                                                                                                                                                                                                    |
| `browser_session` DELETE no longer leaves meetings stuck in `stopping` — runtime-api synthesises exit callback; sweep uses stable `last_progress_at` | **HIGH** (API-customer ops)               | High-volume API customers see meetings hang in `stopping` forever.                                                                                                                                                                                                                                                                                                                                                    |
| One canonical `playback_url` on each recording (dashboard stops choosing)                                                                            | **HIGH** (architecture)                   | Dashboard reverts to selection logic; bug class recurs every new media flavour.                                                                                                                                                                                                                                                                                                                                       |
| Relational `recordings` + `media_files` tables dropped (canonical store is JSONB)                                                                    | **HIGH**                                  | Closes an ambiguity class that has actively caused misinterpretation. The `meetings.py:1992` "legacy inline storage" comment misled this conversation mid-thread; the same comment misleads every contributor walking into the recording code. Customer-visible blast of the action is near-zero (2 stray rows); the value is *architectural clarity*, paid forward to every future audit, refactor, and contributor. |
| GMeet bots fail fast and tell you why (rejection <30s; waiting-room eviction handled)                                                                | **MEDIUM-HIGH** (customer-D Discord pain) | All GMeet bot users see the 120s hang return.                                                                                                                                                                                                                                                                                                                                                                         |
| Post-meeting webhooks fire exactly once per session (idempotency latch via `SELECT FOR UPDATE`)                                                      | **MEDIUM**                                | Webhook integrators see duplicate `meeting.ended` events again.                                                                                                                                                                                                                                                                                                                                                       |
| Voice-agent virtual camera initialises when `voice_agent_enabled=true` — **stop-gap band-aid** ahead of the BotConfig refactor (#246); the `cameraEnabled ⇔ voice_agent_enabled` entanglement persists until v0.10.7. Sibling issues #168 #167 #151 remain open. | **LOW** (band-aid; real significance is bounded by #246) | If the band-aid regresses, voice-agent users with the flag lose camera again — same shape as the original #238 bug. Mitigated by #246 landing in v0.10.7 with the proper unified flag model. |
| Bring-your-own TTS playback dispatches on `Content-Type` (WAV + MP3)                                                                                 | **MEDIUM** (new capability)               | BYO-TTS customers lose multi-format support.                                                                                                                                                                                                                                                                                                                                                                          |
| Multilanguage TTS auto-language detection on basic Piper voices                                                                                      | **MEDIUM** (new capability)               | Non-English TTS drops back to English Piper voice.                                                                                                                                                                                                                                                                                                                                                                    |
| WebM master files carry the EBML SegmentInfo duration — dashboard scrubber interactive on load                                                       | **MEDIUM** (UX)                           | "Scanning packets" delay returns; scrubber stutter on every recording open.                                                                                                                                                                                                                                                                                                                                           |
| Env-gated billing dispatch-check on bot creation (off by default; foundation for usage-based billing)                                                | **MEDIUM** (new capability, gated)        | Programmable per-tenant gating off; cannot ship usage-based pricing pilots until next release.                                                                                                                                                                                                                                                                                                                        |
| Pre-release security dependency blocker pulled into scope — dashboard PostCSS fixed; transcription-service removes vulnerable multipart parser path   | **HIGH** (release blocker)                | Dependency advisory class returns in a release-built surface; mitigation is machine-gated by dependency floor and bounded parser checks.                                                                                                                                                                                                                                                                               |
| Teams "Continue without AV" modal handled                                                                                                            | **MEDIUM** (Teams cohort)                 | Teams bots hang at Join modal.                                                                                                                                                                                                                                                                                                                                                                                        |
| Admin API Swagger UI shows the correct curl header                                                                                                   | **LOW** (docs)                            | Admin API integrators have to read code to find the header.                                                                                                                                                                                                                                                                                                                                                           |
| Apple-Silicon caveat in vexa-lite docs                                                                                                               | **LOW**                                   | Caveat lost; M-series adopters hit silent failures unwarned.                                                                                                                                                                                                                                                                                                                                                          |
| Broad `except Exception:` in `callbacks.finalize` narrowed                                                                                           | **LOW** (internal)                        | Real errors get swallowed again.                                                                                                                                                                                                                                                                                                                                                                                      |
| Misleading "prior chunk count" log line now accurate                                                                                                 | **LOW** (internal)                        | Log line misleads debuggers.                                                                                                                                                                                                                                                                                                                                                                                          |
| Five backlog issues walked with explicit RECONFIRM/CLOSE decisions                                                                                   | **LOW**                                   | Backlog stays unwalked.                                                                                                                                                                                                                                                                                                                                                                                               |
| `tests3/lib/migrations/README.md` documents the hand-rolled migration convention                                                                     | **LOW**                                   | New contributors re-discover the convention from source.                                                                                                                                                                                                                                                                                                                                                              |


## Gaps closed


| Gap                                                                  | Significance                | Blast radius of the closure                                                                            |
| -------------------------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------ |
| G1 Dead relational `recordings` + `media_files` tables               | **HIGH** (closes dead path) | One DROP TABLE migration (`m331`); 2 stray rows archived; rollback `m331-restore`.                     |
| G2 Schema drift between code model and prod schema                   | **MEDIUM**                  | Closed implicitly when G1 lands; no separate action.                                                   |
| G3 Misleading "legacy inline storage" comment + 3 stale docs         | **LOW**                     | One-line comment delete + 3 doc edits.                                                                 |
| G4 Dashboard owns storage-layout selection (`pickMasterMediaFile()`) | **HIGH** (bug class)        | ~30-line dashboard refactor; `pickMasterMediaFile()` deleted; `audio-player.tsx` reads `playback_url`. |
| G6 Silent fallback in dead-table SELECT-then-JSONB path              | **MEDIUM**                  | Deleted with G1.                                                                                       |
| G8 Three docs reference dead infrastructure as live                  | **LOW**                     | Doc edits only (`dependency-audit.md`, `model-split-patterns.md`, `implementation-plan-v2.md`).        |
| G9-partial Undocumented hand-rolled migration convention             | **LOW**                     | One README authored; full drift-detection prove deferred to v0.10.7.                                   |


## Decisions made


| ADR                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Significance    | Blast radius if wrong                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ADR-1 JSONB is canonical; drop relational tables                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | **HIGH**        | If JSONB can't carry future needs, full re-architecture; recovery in weeks.                                                                                    |
| ADR-2 Canonical `playback_url`; dashboard becomes pure renderer                                                                                                                                                                                                                                                                                                                                                                                                                                                   | **MEDIUM-HIGH** | If field semantics are wrong, dashboard refactor + API contract bump.                                                                                          |
| ADR-3 No alembic; document hand-rolled `m<NNN>` convention via README                                                                                                                                                                                                                                                                                                                                                                                                                                             | **MEDIUM**      | If drift goes uncaught, single bad migration corrupts data; reversible by adopting alembic.                                                                    |
| ADR-4 **Test everywhere — lite + compose + helm — this release.** Refuse a colour-blind gate; helm IS production, lite + compose green tells us nothing about helm-only items. Customer-impact items (customer-A `/speak`, multichunk backfill, DELETE-stuck) all sit in the helm-bound bucket; deferring helm = deferring customer relief. The earlier framing ("first-time canonical gate under release pressure = wrong trade") had it inverted — pressure to skip the gate is when the gate is most valuable. | **HIGH**        | If helm rehearsal surfaces unforeseen issues, release timeline extends (estimated ~1 week instead of 3-5 days). Acceptable cost vs shipping unverified claims. |
| ADR-5 Multi-bot / multi-session stays dormant                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | **LOW**         | No change from today; capability stays unused.                                                                                                                 |
| ADR-6 `scope.md` co-authored with `scope.yaml` this release                                                                                                                                                                                                                                                                                                                                                                                                                                                       | **LOW**         | Tooling pivot delayed one release; fully reversible.                                                                                                           |
| ADR-7 Drop arm64 multi-arch claim; reopen as v0.10.7 good-first-issue                                                                                                                                                                                                                                                                                                                                                                                                                                             | **LOW**         | CI keeps publishing vexa-lite manifests quietly; no customer-facing claim until verified.                                                                      |


## Trade-offs acknowledged


| Trade                                      | Chosen                           | Significance   | Cost I accept                                                                                          |
| ------------------------------------------ | -------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------ |
| Speed vs scope (20 items + helm rehearsal) | Scope + correctness              | **HIGH**       | ~1 week to release vs ~3-5 days for the smaller cut; first-time helm rehearsal absorbed in this cycle. |
| Customer-A wait vs do helm now             | Do helm now                      | **HIGH**       | Release timeline grows; in return, customer-A's `/speak` fix ships in v0.10.6.1.                       |
| Cleanup now vs another release of drift    | Cleanup now                      | **MEDIUM**     | One `DROP TABLE` migration this release; three docs touched.                                           |
| Hand-rolled migrations vs alembic          | Hand-rolled stays                | **LOW**        | Schema-drift class possible until v0.10.7's detection prove.                                           |
| Multi-bot wired vs dormant                 | Dormant                          | **LOW**        | `session_uid` infrastructure continues unused.                                                         |
| `scope.md` primary now vs co-author        | Co-author both, pivot in v0.10.7 | **LOW**        | Dual-author cost for one release.                                                                      |
| Untested arm64 claim vs verified later     | Verified later                   | **LOW-MEDIUM** | Apple-Silicon adoption friction continues until v0.10.7 ships verified arm64.                          |


## Deferred


| Item                                                                          | Target                     | Significance   | Blast of further delay                                                                       |
| ----------------------------------------------------------------------------- | -------------------------- | -------------- | -------------------------------------------------------------------------------------------- |
| Verified arm64 multi-arch images (runtime tested + extended beyond vexa-lite) | v0.10.7 (good-first-issue) | **LOW-MEDIUM** | Apple-Silicon contributor friction continues; vexa-lite manifest ships but stays unverified. |


## Next-cycle commitments (v0.10.7)

`botconfig-capability-split` (#246 — splits `BotConfig` into `MeetingRequest` + `BotProfile` + `BotRuntime` + `BotSession`; resolves the 4-store precedence chain + the `cameraEnabled ⇔ voice_agent_enabled` entanglement + the three-name inconsistency for recording-enable) · `recording-video-validation-and-dods` (#262 — depends on #246) · `bots-per-meeting-add-remove` · `media-files-removal-from-public-api` · `schema-drift-detection` · `recordings-storage-prefix-as-separate-field` · `state-machine-docs-rewrite` · `scope-md-pivot` · `arm64-multiarch-validated`

---

## Current develop-human status report

Status: machine-owned local handoff is green for `develop-human`; human signoff is still incomplete and must not be pre-filled by AI.

Handoff targets ready for human validation:

- Lite dashboard: `http://localhost:3100/login`
- Lite gateway: `http://localhost:8156/`
- Compose dashboard: `http://localhost:3001/login`
- Compose gateway: `http://localhost:8056/`
- Compose admin API: `http://localhost:8057/docs`
- Compose TTS endpoint: `http://localhost:8002/v1/audio/speech`

What is done and how it is proven:

| Done | How it was delivered | Machine proof |
| --- | --- | --- |
| Local proof gate is no longer colour-blind | Added a scope proof gate that requires every local `scope.yaml` proof cell to have a passing report before human handoff | `SCOPE_LOCAL_PROOFS_ALL_GREEN`: 35 LOCAL scope proof cells green, 0 deferred |
| Human receives working deployment URLs, not abstract targets | Local mechanical gate checks target URLs, container health, transcription-lb readiness, recent logs, memory, env parity, stale containers, dropped recording tables, and lite recording file presence | `LOCAL_HUMAN_*`: all green in `local-human-mechanical-gate.json` |
| The dashboard auth bug is covered | Dashboard auth cookies are deployment-specific (`vexa-token-lite`, `vexa-token-compose`) and stale detail-page auth is cleared instead of surfacing raw upstream API-key errors | `DASHBOARD_BROWSER_MEETINGS_AUTH_OK`, `DASHBOARD_DETAIL_STALE_AUTH_RECOVERS`, `DASHBOARD_AUTH_COOKIES_ISOLATED` |
| The reported compose playback failure is covered | Browser harness opens `http://localhost:3001/meetings/10099`, requires the same-origin master recording proxy route to load audio metadata, and fails if the page still renders `Recording is processing...` or `Preparing audio...` | `DASHBOARD_COMPLETED_RECORDING_PLAYBACK_READY`: meeting `10099` rendered playback, not processing |
| The reported transcript-pipeline failure is covered | Live transcript probe checks the configured delivery meeting/newest qualifying admitted bot with recording chunks and verifies `GET /transcripts` has segments | `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT`: meeting `10099` has 6 chunks and 17 transcript segments |
| Transcription auth is no longer trusted by configuration shape alone | Smoke test posts real sample audio through the deployed runtime path and requires `HTTP 200` plus non-empty segments | `SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP`: green in lite and compose |
| The bad token fallback pattern is explicitly blocked | `deploy/compose/.env` is the local deployment SSOT for transcription URL/token; local deploy writes both compose and lite from that file, and smoke verifies deployed runtime env matches it. No caller-env override, host-file scavenging, generated-env scavenging, local/dev/example token fallback, or fake token strings in the local handoff path | `TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK`: green in lite and compose |
| Hardenloop added to dev audit and run locally | Release-cycle docs now make Hardenloop part of `develop-audit`, after LOCAL validate and before `develop-human`; local source `/home/dima/dev/vexa-i-adversarial-harness` was used. Committed dashboard validation tokens found by Hardenloop were removed and replaced with `VEXA_DASHBOARD_TEST_TOKEN` fail-closed reads. | Non-full receipt: `tests3/releases/260508-v0.10.6.1/hardenloop-20260514-final/` (`285` findings, `0` normalized release blockers; remaining high items are placeholder/test examples). Full bundled-tool scanner run is not green: `tests3/releases/260508-v0.10.6.1/hardenloop-20260514-full-scan-summary.md` records `7197` Gitleaks blockers and forces bounce to `develop-code`. |
| The pulled-in security advisory dependency blocker is covered | Dashboard PostCSS resolves to `8.5.10`; transcription-service no longer installs `python-multipart` and its OpenAI-compatible upload route uses a bounded standard-library multipart parser instead of FastAPI multipart helpers | `PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS`: green in lite and compose; source/image build check confirms `python-multipart` is absent from the transcription-service release image |
| Lite pre-fix broken evidence is quarantined | Meeting `171` was identified as pre-fix: chunks recorded, transcription calls returned `HTTP 401`, and old container-local recording files were unrecoverable after redeploy. The stale lite rows were backed up and removed from active handoff evidence | Backup: `tests3/.state-lite/backups/pre-human-gate-stale-lite-meetings-20260513-145620.sql`; do not use meeting `171` as approval evidence |
| Human checklist contains only human-judgment work | Machine-checkable items moved into registry proofs; human brief now asks the human to validate actual browser/product behavior, real meeting transcript, playback/audibility, and TTS heard in-meeting | `local-human-brief.md` and `local-human-checklist.yaml` updated |

Current evidence files:

- `tests3/.state/reports/none/scope-proof-gate-local.json`
- `tests3/.state-compose/reports/compose/local-human-mechanical-gate.json`
- `tests3/.state-compose/reports/compose/live-bot-transcript-pipeline.json`
- `tests3/.state-compose/reports/compose/dashboard-recording-playback-ready.json`
- `tests3/.state-compose/reports/compose/no-placeholder-transcription-token.json`
- `tests3/.state-lite/reports/lite/no-placeholder-transcription-token.json`
- `tests3/.state-compose/reports/compose/advisory-dependency-floors.json`
- `tests3/.state-lite/reports/lite/advisory-dependency-floors.json`
- `tests3/.state-compose/reports/compose/smoke-bot-transcription-roundtrip-roundtrip.json`
- `tests3/.state-lite/reports/lite/smoke-bot-transcription-roundtrip-roundtrip.json`
- `tests3/releases/260508-v0.10.6.1/hardenloop-20260514-final/`
- `tests3/releases/260508-v0.10.6.1/hardenloop-20260514-full-scan-summary.md`

What remains human-only before signing `develop-human`:

- Log in with `test@vexa.ai` on lite and compose and confirm the UI is usable, not just API-reachable.
- Create or open a real Google Meet, join a bot from compose, admit it if needed, talk for 30-60 seconds, and confirm the spoken words appear as transcript text.
- Stop the bot and confirm the meeting leaves active/stopping states cleanly.
- Confirm recording playback is audible, scrub-able, full-duration, and not truncated to an early chunk.
- Run the speak/TTS flow and confirm the phrase is actually heard in the meeting.
- Optional platform edge checks: Teams Continue-without-AV, Google Meet host-not-started fast-fail, and voice-agent camera behavior.

Human checklist: [local-human-checklist.yaml](/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/local-human-checklist.yaml)

---

## Open questions — block sign

**Q1. Resolved (Option C).** Helm-bound items pulled back into v0.10.6.1; helm is rehearsed in this release. See ADR-4.

**Q2. Walkthroughs requested before sign.** The signer asked to step through these in detail:

- The finalizer master-path race fix
- The GMeet rejection / waiting-room behaviour
- The voice-agent `cameraEnabled` fix

Pulled-in helm-bound items also worth walking if you want their detail:

- `/speak` outage (customer-A) — TTS pod boot + bot provider-param
- Multichunk backfill — reader picks master vs chunk-0 + the backfill script for ~73 historical recordings
- `browser_session` DELETE stuck-in-stopping — synthesised exit callback + sweep predicate change
- Post-meeting webhook idempotency — `SELECT FOR UPDATE` latch
- WebM EBML duration injection at finalize
- Env-gated billing dispatch-check

---

```yaml
what_we_are_doing:
```

- cleanup sloppy recording storage and retrieval pipeline and schema
- delivering TTS in prod (users ask)
- Lite Mac support
- browser session stuck fix
- **GMeet rejection fast-fail**
- **Post-meeting webhook idempotency - super important billing issue**  




```yaml
why_right_call_given_current_state:
```



- mininal thinkgs before 0.10.7



```yaml
what_im_uncertain_about_and_what_would_change_my_mind:
```



- the entire schema for recording enables might be messy and we are accepting the PR about that, will we refactor later anyway



## Sign

Canonical block per `tests3/sign-template.md`. The doc travels the cycle and is signed FOUR times — at plan-human, develop-human, stage-human, and release. Each sign attests against the current revision (pinned by `git_sha`); earlier signs remain in the audit trail. AI does not pre-fill any field.

```yaml
signs:

  # ─── Sign 1 — exit plan level ─────────────────────────────────────
  - stage: plan-human
    signer: dmitry@vexa.ai
    signed_at: 2026-05-12T11:48:25Z
    attestation_confirmed:
      read_multiple_times: true
      understands_what_and_why: true
      confirms_balance_deliverable: true
      i_authored_this_doc: true
    signed_artefact:
      path: tests3/releases/260508-v0.10.6.1/scope.md
      revision: v3 (2026-05-12; option-C helm-rehearse + option-B band-aid)
      git_sha: bf3487c1c263bb1031f1a1e0425ad2786cc34de5
    rationale_in_my_own_words:
      what_we_are_doing: |
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |

  # ─── Sign 2 — exit dev level ──────────────────────────────────────
  - stage: develop-human
    signer: ""
    signed_at: ""
    attestation_confirmed:
      doc_still_describes_reality: false
      local_validation_green: false
      local_human_checklist_walked: false
      i_authored_any_new_prose: false
    signed_artefact:
      path: tests3/releases/260508-v0.10.6.1/scope.md
      revision: ""           # fill at sign time; may equal sign 1 if doc unchanged
      git_sha: ""
    rationale_in_my_own_words:
      what_we_are_doing: |
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |
    notes_since_prior_sign: |

  # ─── Sign 3 — exit stage level ────────────────────────────────────
  - stage: stage-human
    signer: ""
    signed_at: ""
    attestation_confirmed:
      canonical_validate_matrix_green: false
      code_review_approved: false
      canonical_stack_eyeroll_approved: false
      doc_still_describes_reality: false
      i_authored_any_new_prose: false
    signed_artefact:
      path: tests3/releases/260508-v0.10.6.1/scope.md
      revision: ""
      git_sha: ""
    rationale_in_my_own_words:
      what_we_are_doing: |
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |
    notes_since_prior_sign: |

  # ─── Sign 4 — exit release level ──────────────────────────────────
  - stage: release
    signer: ""
    signed_at: ""
    attestation_confirmed:
      merged_to_main: false
      git_tag_pushed: false             # v0.10.6.1
      images_promoted_to_latest: false
      env_example_fixed_on_main: false
      release_notes_match_what_shipped: false
    signed_artefact:
      path: RELEASE_NOTES.md            # post-rename at release stage
      revision: ""
      git_sha: ""
    rationale_in_my_own_words:
      what_we_are_doing: |
      why_right_call_given_current_state: |
      what_im_uncertain_about_and_what_would_change_my_mind: |
    notes_since_prior_sign: |
```
