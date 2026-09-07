# Terminal seam scenario catalog

Behavior-named scenarios for the **terminal integration surface** — the client that couples the
**meetings** domain (live transcript, bot actions) and the **agent** domain (chat/research). These terminal↔{meetings,agent} seams are the most recently churned code
(`catch-up cursor`, `1:1 buildProcessedNotes`, `Terminal Auth-A`) yet had no catalog row, so a
regression there turned no row red. This catalog fixes that. Sibling of `meeting-seams.md` (the
meetings-internal failure classes); see `README.md` for the split.

`status` ∈ 🟢 green (probe opened, asserts the expected state — P8) · 🟡 in-flight (probe exists, named
gap not yet asserted) · 🔴 open (no probe yet). A row is `green` ONLY after opening the named probe and
confirming it asserts the EXACT expected state — never on a similarly-named test's mere existence.

## Why these seams are cheaply testable

Every data carrier has exactly ONE writer (P23) and crosses a process boundary as a pinned contract
(P4). So a synthetic frame written to a carrier on one side and validated against the contract on the
other is deterministic — no live meeting, no browser, and (for the floor) no model.

## The two rigs

```text
RIG A — data-plane (deterministic, no UI):
  INJECT   tc:meeting:{numeric}:mutable | transcription_segments
  VALIDATE agent-api /api/meeting/stream SSE bytes  +  tc:meeting:{native} stream contents
  HOME     core/agent/services/agent-api/tests/ (extends test_transcription_watcher.py, test_api.py)

RIG B — SSE → render (pure function):
  INJECT   synthetic SSE / ws.v1 frames
  VALIDATE LiveTranscriptEngine output blocks + meetingLive store
  HOME     clients/terminal/ vitest (extends meeting/stream/route.test.ts, meetingLiveMapping.test.ts,
           liveTranscriptEngine.test.tsx, gatewayWS.golden.test.ts)
```

Deterministic floor first (T1–T8, no model calls) → `gate:eval` rows. Judge tier (T9–T12) is `open`,
deferred to an L4 eval with a frozen transcript fixture.

```yaml
# ── Tier 1 — deterministic, highest blast radius (data-plane / Rig A) ─────────
- id: terminal-raw-transcript-fidelity-no-rederive
  status: green
  seam: "tc:meeting:{numeric}:mutable -> transcription_watcher relay -> tc:meeting:{native} -> SSE"
  module_probe: core/agent/services/agent-api/tests/test_transcription_watcher.py  # test_relay_fans_confirmed_and_pending, test_recycled_seq_distinct_utterances_both_survive, test_relay_uses_stamped_native_id_with_empty_keymap, test_seed_fans_store_in_start_order
  expected:
    recycled_segment_id_across_utterances: both_survive   # distinct full-ids; the old re-derive overwrote the first
    confirmed_and_pending: both_fanned                    # completed:true AND draft both relayed
    rekey: numeric_to_native                              # off the collector-stamped native_id (no user-scoped lookup)
    ordering: by_start                                    # earliest start anchors; no scramble
    loss: none
  note: >
    P23 single-writer: the watcher RELAYS the collector's already-deduped feed (never re-derives), so
    every distinct segment is fanned once under its own id. The watcher docstring cites the recycled-id
    lost-lines bug as the historical regression this pins.

- id: terminal-catchup-cursor-gapless-reconnect
  status: green
  seam: "agent-api /api/meeting/stream (Last-Event-ID resume) -> gateway passthrough -> terminal proxy -> meetingLive"
  module_probe: core/agent/services/agent-api/tests/test_api.py  # test_sse_resumes_from_last_event_id_no_reseed, test_sse_fresh_connect_seeds_and_tails, test_sse_cursor_encode_decode_roundtrip, test_meeting_stream_seeds_recent_tail_without_replaying_from_zero
  seam_probe: clients/terminal/src/app/api/meeting/stream/__tests__/route.test.ts  # "forwards Last-Event-ID upstream (header AND ?lid= param)"
  expected:
    last_event_id_format: "{transcript_id}|{output_id}"
    reconnect_with_cursor: delivers_K+1..N_exactly_once    # zero gap, zero dup; resumes from the cursor, NOT re-seed
    fresh_connect_replay: { transcript: 80, output: 160 }  # MEETING_STREAM_TRANSCRIPT_REPLAY / OUTPUT_REPLAY, then tail "$"
    proxy_forwards_cursor: [ "Last-Event-ID header", "?lid= query param" ]
  note: >
    THE real-time transcript-loss bug: the live SSE resumed from "$" + an 80-entry seed, so a reconnect
    dropped the gap beyond 80 from the live view (durable store kept it -> reappeared post-time only).
    Fix = resumable SSE via Last-Event-ID across agent-api + terminal proxy + the engine's forceReconnect.

- id: terminal-multi-meeting-isolation-late-and-never-resolve
  status: green
  seam: "two concurrent numeric meetings -> _resolve_native keymap-freeze -> separate tc:meeting:{native}"
  module_probe: core/agent/services/agent-api/tests/test_transcription_watcher.py  # test_two_distinct_meetings_stay_separate, test_late_native_resolution_does_not_fork_or_collapse, test_resolve_native_returns_only_the_matched_id, test_unresolved_meeting_surfaces_under_numeric_after_grace
  expected:
    immediate_resolve: own_native_stream
    late_resolve: no_fork                          # key frozen once; never flips mid-stream
    never_resolve: held_during_grace_then_surfaces_under_numeric   # asserted: never swallowed
    cross_contamination: none
  note: >
    Distinct, late-resolve, AND never-resolve (held during RESOLVE_GRACE_SEC then keyed numeric, not
    swallowed) are all asserted — the last via a controllable monotonic clock.

- id: terminal-live-view-is-the-raw-transcript
  status: green
  seam: "tc:meeting:{row} -> agent-api /api/meeting/stream -> MeetingCanvasView"
  module_probe: core/agent/services/agent-api/tests/test_api.py  # test_meeting_stream_carries_no_copilot_lane, test_the_retired_copilot_endpoints_are_gone
  seam_probe: clients/terminal/src/canvas/__tests__/minutesLiveView.test.tsx
  expected:
    feed: transcript_and_retract_only              # no note / card / model-error rides it
    controls: none                                 # no processing toggle, no model chips
    retired_endpoints: 404                         # /api/meeting/{start,process}
  note: >
    PRD decision 34 replaced the row that stood here (terminal-processing-toggle-opt-in-copilot: the
    proc:meeting:{key}:on flag -> watcher arm -> copilot dispatch). The product runs no model calls of
    its own beside the agent, so the seam is one stream with one producer.

- id: terminal-backseed-history-on-restart
  status: green
  seam: "meeting:{numeric}:segments store -> watcher cold start -> _seed_from_store -> tc:meeting:{native}"
  module_probe: core/agent/services/agent-api/tests/test_transcription_watcher.py  # test_seed_fans_store_in_start_order, test_back_seed_is_idempotent_across_rehandles, test_back_seed_carries_absolute_timestamp
  expected:
    cold_start: full_history_fanned_once           # idempotent: re-seed does not duplicate
    order: by_start
    absolute_timestamp: carried                    # renderer skips segments without absolute_start_time
  note: An in-progress meeting (or an agent-api restart) shows its history immediately, exactly once (4-batch re-handle stays at 2 segments).

- id: terminal-session-end-drops-the-live-row
  status: green
  seam: "session_end on transcription_segments -> _handle -> tc:meeting:{native} session_end + live.drop + keymap clear"
  module_probe: core/agent/services/agent-api/tests/test_transcription_watcher.py  # test_session_end_drops_the_live_row_without_writing_the_carrier
  expected:
    fanned_frame: { type: session_end, uid: "{native}" }
    live_row: dropped
    keymap_and_seed: cleared                        # a same-numeric relaunch re-resolves cleanly (no stale key)
  note: session_end is the last transcript entry; reaping clears all per-meeting state so a relaunch is a fresh meeting.

# ── Tier 2 — deterministic, security & fault (Rig A + B) ──────────────────────
- id: terminal-p20-complete-mediation
  status: in-flight
  seam: "user key -> gateway X-User-Id inject -> agent-api subject derive -> canAccess(transcript|proc|workspace)"
  module_probe: core/identity/tests/test_access.py            # canAccess deny
  seam_probe: core/gateway/services/gateway/tests/test_proxy.py   # X-User-Id injection / anti-spoof + test_meeting_stream_denies_a_meeting_the_user_does_not_own (xfail executable-spec: flips RED when the SSE authz lands)
  expected:
    client_supplied_subject: ignored               # subject derived from header, never body (P20) — GREEN
    sessions_cross_user: deny                       # GREEN (test_chat_subject_is_server_derived_from_header_not_client_body)
    no_key: 401
    wrong_scope: 403
    meeting_sse_cross_user: deny                    # GAP (FINDING): /api/meeting/stream has NO per-meeting authz
  note: >
    Subject-derivation + session cross-user denial are GREEN (test_api.py: server-derived subject, the
    spoofed body owns nothing). OPEN GAP / SECURITY FINDING: the live-transcript SSE
    gateway `/api/meeting/stream` only resolves the key→user (X-User-Id inject) but does NOT authorize
    that the requested meeting belongs to the user — any authenticated user can stream any meeting's
    transcript by passing its native id. Needs the same authorize_subscribe ownership check the WS uses.
    Row stays in-flight until that authz lands (writing the deny-test now would assert unimplemented code).

- id: terminal-fault-surfacing-never-silent
  status: in-flight
  seam: "STT/worker fault -> typed error carrier -> SSE -> meetingHealth verdict"
  module_probe: clients/terminal/src/canvas/__tests__/meetingHealth.test.ts   # stalled / disconnected / model-error verdicts
  seam_probe: core/agent/services/agent-api/tests/test_meeting_postprocess_offline.py  # test_offline_card_turn_emits_auth_error_on_401_done_reply (+ streamed, + non-auth generic)
  expected:
    worker_model_auth_401: typed_auth_error_event   # green
    silent_stream_gt_threshold: stalled_verdict     # green (isTranscriptStale)
    disconnected: disconnected_verdict              # green
    stt_402_payment_required: surfaced_to_terminal  # GAP: bot pipeline onError is console-only (index.ts) — dies before any carrier
  note: >
    Worker auth-error + terminal health verdicts are green. The STT typed faults (TranscriptionError:
    payment_required/unauthorized/unavailable/timeout) are NOT surfaced — the bot composition root logs
    onError to console and never publishes a health frame, so the terminal shows silence, not a fault.
    This row stays in-flight until the bot emits STT faults to a carrier the SSE can forward (P18/P21).

- id: terminal-bot-action-roundtrip-status-normalize
  status: green
  seam: "terminal add-bot (URL->native) -> POST /bots -> bm:meeting status frames -> meetingLive badge"
  seam_probe: clients/terminal/src/surfaces/__tests__/meetingActions.test.tsx   # action matrix per status + launch/stop/intent POST targets
  module_probe: clients/terminal/src/surfaces/__tests__/meetingLiveMapping.test.ts
  compose_probe: clients/terminal/src/surfaces/__tests__/gatewayWS.golden.test.ts   # flat + PURELY-nested normalize identically + requested->active->completed progression
  expected:
    status_progression: requested -> active -> completed   # parseFrame yields the flat status for each
    frame_shapes_normalize_identically: ["{meeting_id,native,status} (flat)", "nested meeting.status"]
    actions_per_status: { active: [stop], completed: [resend], idle: [schedule, send] }
  note: >
    Status normalization (flat AND purely-nested → identical flat frame) + the progression + the
    per-status action matrix + launch/stop/intent POST targets are all asserted. (Bot-side teardown/no-orphan
    is a meetings-internal concern — see meeting-seams.md stop-active-bot reconcile row.)

# ── Tier 3 — judge-based, deferred (L4 eval, frozen transcript fixture) ───────
- id: terminal-tag-entity-selection
  status: open
  seam: "worker -> inline entity tags -> render contract"
  expected:
    deterministic_half: tags_conform_to_render_contract
    judge_half: right_entities_no_spurious_tags

- id: terminal-canvas-action-research-commit
  status: open
  seam: "canvas action -> chat/research dispatch -> SSE turn -> commit kg/entities/<kind>/<slug>.md"
  expected:
    deterministic_half: turn_streams_and_commits_doc_at_expected_path
    judge_half: doc_is_correct_sourced_summary

# ── Error presentation + control gating (issue #533 / #674 rows) ──────────────
- id: terminal-error-presentation-user-truth
  status: green
  seam: "ApiError {status,detail,url} -> presentError -> surface alert regions (8 surface files)"
  module_probe: clients/terminal/src/surfaces/__tests__/presentError.test.ts  # fixture range: network 0, 502/504, 401, 403, 422-json, 429, typed 503 prose (verbatim pass-through), empty detail
  seam_probe: clients/terminal/src/surfaces/__tests__/errorPresentation.guard.test.ts  # grep-guard: no surface renders the raw `e instanceof Error ? e.message : String(e)` idiom
  expected:
    headline: user_vocabulary_never_transport_plumbing   # no url/status/exception-name in the rendered line
    typed_backend_detail: passes_through_verbatim        # a prose 4xx/5xx detail is the backend's own user-facing reason
    plumbing: preserved_on_detail_and_console            # P18's observable channel keeps the full string; the error object is never mutated
  note: >
    The presenter seam lives beside ApiError (apiClient.ts). Surfaces render presentError(e),
    never e.message; the grep-guard makes the 46th raw site impossible to land silently.

- id: terminal-header-botcontrol-follows-ws
  status: green
  seam: "ws.v1 meeting.status connectivity -> liveMeetings store (useLiveMeetingsConnection) -> meeting header BotControls"
  module_probe: clients/terminal/src/surfaces/__tests__/liveMeetings.store.test.tsx  # (e) open->true, close->false, reopen->true + re-snapshot
  seam_probe: clients/terminal/src/surfaces/__tests__/meetingHeaderControls.test.tsx  # header state pure fn + disabled "Stop bot" while disconnected
  expected:
    connected_active: stop_enabled
    disconnected: indeterminate_disabled_reconnecting    # a stale-live snapshot can never present an actionable "Stop bot"
    action_404: human_message_plus_reconciling_resnapshot  # never the raw JSON body (meetingActions.test.tsx)
    action_409: human_already_has_bot_line

```
