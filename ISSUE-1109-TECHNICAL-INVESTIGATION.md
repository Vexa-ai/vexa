# Issue #1109 — Technical Investigation

## Scope

This document records the deep technical investigation of [Vexa Issue #1109](https://github.com/Vexa-ai/vexa/issues/1109): a bot dispatched to a nonexistent meeting can be reported as **“stopped by the user”**, even though the user never issued a stop request.

The investigation was performed on branch `codex/issue-1109`, based on `origin/main`. It covers the Python/FastAPI meeting service, the meeting lifecycle reconciler, the bot-spawn adapters, the Google Meet join module, and the runtime callback boundary.

This is an engineering investigation, not a claim that the current branch completely fixes the issue.

## Executive summary

The misleading message is generated in the Python meeting lifecycle reconciler. When a workload disappears before it is admitted to a meeting, the reconciler creates a synthetic terminal event. If the meeting row contains `stop_requested=True`, it converts that flag into:

```text
stopped by the user at <status> (never admitted)
```

That behavior is correct for a real user-initiated stop, but the investigation found two separate problems and one important limit on what can be proven from this checkout:

1. **Stale stop state can survive when a terminal meeting is continued.** The old meeting row is reused, and its row-level `stop_requested` flag can be carried into the replacement workload. This can make a later workload look like a user-stopped workload. The current branch fixes this state-reset problem and adds regression coverage.

2. **A clean fresh invalid-meeting failure does not set `stop_requested` in the current code.** The exact `requested` + “stopped by the user” combination therefore requires either a user-stop write, stale state from a reused row, a race involving a stop request, or behavior from a deployed version/configuration not represented by this checkout. The public issue data does not include the affected rows’ `data`, transition history, runtime event, or bot log, so the exact production trigger cannot be proven from the issue alone.

3. **There is a separate, confirmed error-classification bug.** The Google Meet join code detects pages such as “Meeting not found,” but maps the generic `error_page` result to **“Bot admission was rejected by meeting admin.”** This is misleading, but it is not the same string as #1109’s “stopped by the user” message and should not be presented as the proven trigger for #1109.

The strongest complete solution therefore needs the Python hardening already added, better attribution of stop intent, and better propagation/classification of the actual join failure. The Python service should never infer a user stop solely from an ambiguous, stale, or system-generated boolean flag.

## Issue behavior

The issue reports four short-lived workloads attached to meeting IDs `25819`, `25820`, `25821`, and `25823`. They lived for roughly one second and were reported with a message similar to:

```text
stopped by the user at requested (never admitted)
```

The issue contrasts those failures with a genuine user withdrawal, which lasted much longer and was reported as a stop while awaiting admission. The issue also states that the failed nonexistent-meeting cases were not billed because the bot never joined.

The follow-up comment narrows the examples to synthetic/test-looking meeting IDs and asks for a message such as **meeting not found** or **could not be joined**, rather than a user-stop explanation. See the [issue](https://github.com/Vexa-ai/vexa/issues/1109) and its [follow-up comment](https://github.com/Vexa-ai/vexa/issues/1109#issuecomment-5236529528).

The follow-up comment gives a useful operational clue, but not a code-level explanation: the short-lived failures used IDs such as `test-native-123`, `test-cf-123`, `zzz-yyyy-xxx`, and other obviously synthetic values. It reports 65 meetings using `test%` IDs, with 49 failures and 16 completions, and shows the same user succeeding with a real meeting ID between two sub-second `test-native-123` failures. The comment also says the synthetic IDs were not found in the Vexa or Vexa-platform repositories. That makes an external tutorial/test caller, invalid-input path, or test harness worth investigating; it does **not** prove that Vexa generated the IDs or that the browser actually saw a “Meeting not found” page.

The issue is an operations report, not a complete trace. It does not show the affected rows’ `stop_requested` value, `status_transition` trail, `service_authority` record, runtime event payload, or bot terminal lifecycle callback. Those missing facts are exactly what would be needed to distinguish stale state, a race, an external stop, or a deployed-version difference.

## Relevant architecture

The failure crosses several components:

```text
API request
  -> meeting-api creates or reuses a meeting row
  -> bot-spawn launches the runtime workload
  -> join module opens Google Meet and tries to enter
  -> runtime emits lifecycle/destroy callbacks
  -> meeting-api reconciles the workload state
  -> API response exposes the stored terminal reason
```

### Python/FastAPI meeting service

The meeting service owns the persisted meeting lifecycle. It stores statuses such as:

- `requested`
- `joining`
- `awaiting_admission`
- `active`
- `stopping`
- terminal states such as `completed` or `failed`

The service also stores lifecycle metadata in the meeting record, including `stop_requested`, `completion_reason`, and `failure_stage`.

### SQL and fake repository adapters

The SQL adapter persists meeting state in the database. The fake adapter mirrors that behavior for unit tests. Both adapters expose the same conceptual operations used by the lifecycle service, including reopening a meeting and claiming a planned workload.

### Runtime and lifecycle reconciliation

The runtime can destroy a workload before the bot sends a normal terminal lifecycle callback. The meeting service then has to reconcile the missing workload and synthesize a terminal event. This fallback is important because it is the path that produces the issue’s “never admitted” wording.

There are two separate callback contracts:

- `runtime.v1` is the kernel/container lifecycle. It reports workload states such as `starting`, `running`, and terminal states such as `destroyed` or `stopped`, with optional `exitCode` and `stopReason`.
- `lifecycle.v1` is the bot’s domain lifecycle. It reports `joining`, `awaiting_admission`, `active`, and terminal meeting outcomes with `completion_reason`, `failure_stage`, and free-form `reason` text.

The runtime callback is a backstop. It is not the normal source of a browser-specific join diagnosis.

### Google Meet join module

The join module is TypeScript rather than Python. It drives the browser, detects Google Meet page states, and converts browser outcomes into join outcomes such as success, rejection, or join failure. Those outcomes can carry a more specific reason, but that information is not always available to the Python fallback path.

## Workload and session-correlation audit

The placeholder meeting IDs in the issue are not themselves used as runtime workload IDs. The Python spawn service constructs a workload ID as:

```text
mtg-<database meeting id>-<first 8 characters of a fresh connection UUID>
```

The connection UUID is also the `MeetingSession.session_uid`, so every normal spawn gets a fresh workload suffix even when the external/native meeting ID is reused. The SQL `find_by_container` query uses exact equality on `Meeting.bot_container_id`; it does not search by the Google Meet/native ID. Therefore, this checkout has no obvious direct collision where two `test-native-123` values alone would make one workload resolve to another meeting.

There is still a structural correlation weakness: `meetings.bot_container_id` stores only one workload per meeting, while `meeting_sessions` stores multiple sessions but does not store the workload ID. After finding a meeting by workload, `find_by_container` chooses the latest session for that meeting rather than a session explicitly linked to that workload. A late callback, an overwritten container ID, or a continued run can therefore be misattributed if events arrive out of order. This is a credible hardening target, but it does not explain the reported fresh rows without production evidence showing that the mapping was overwritten or stale.

## Exact source of the misleading message

The exact phrase is generated by `synthesize_terminal_for_dead_workload` in:

[reconcile.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/lifecycle/reconcile.py:524)

The relevant logic is conceptually:

```python
info = await repo.find_by_container(bot_container_id=workload_id)
stop_requested = bool(info.get("stop_requested"))

if status in _PRE_ACTIVE_STATUSES:
    completion_reason = _pre_active_completion_reason(status, stop_requested)

    if stop_requested:
        reason = f"stopped by the user at {status} (never admitted)"
```

The important detail is that the reconciler does not independently verify who requested the stop. It trusts the persisted boolean. Therefore, any stale, mis-scoped, or incorrectly produced `stop_requested=True` value is enough to produce a user-attributed message.

However, this is an attribution vulnerability, not proof that invalid meeting IDs set the flag. In the current checkout, the reconciler only consumes the flag; it does not create it.

## What a normal invalid Google Meet should do

The current bot code gives a useful negative test for the #1109 hypothesis.

For a Google Meet page that visibly matches one of the configured error indicators, the expected path is:

```text
Google Meet page shows an error indicator
  -> classifyGoogleRejection() returns error_page
  -> waitForGoogleMeetingAdmission() throws AdmissionError("denial", message)
  -> createBrowserJoinDriver() catches AdmissionError
  -> driver returns { outcome: "rejected", reason: message }
  -> orchestrator emits failed(awaiting_admission,
       completion_reason=awaiting_admission_rejected,
       reason=<message>)
  -> meeting-api persists that bot lifecycle event
```

That path does not write `stop_requested`. It should not produce `completion_reason="stopped"` or the text **“stopped by the user.”**

If the normal bot terminal callback is lost and the runtime callback is the only evidence that arrives, the Python fallback instead sees a terminal workload plus the persisted meeting stage. With no stop marker, it synthesizes a generic non-user reason such as `workload destroyed before the bot reported (never started)` for `requested`/`joining`, or `workload destroyed while awaiting admission (never admitted)` for `awaiting_admission`. It still does not produce the user-stop text.

This is why the exact Issue #1109 symptom is diagnostic of an additional stop-state or correlation problem. Invalid-meeting detection alone is insufficient to explain it in this repository version.

## How the reason reaches the API projection

The synthetic event carries a free-form `reason` field. The lifecycle machine stores that value in its in-memory record. For a failed terminal event, if an explicit `error_details` value was not supplied, the machine also builds an error-details string from the exit code and reason.

The record’s `data` projection stores this as a `last_error` object containing:

```json
{
  "exit_code": 1,
  "reason": "stopped by the user at requested (never admitted)",
  "error_details": "Bot exited with code 1; reason: stopped by the user at requested (never admitted)"
}
```

The issue’s shorthand `last_error = "..."` may therefore refer to the nested `reason` value, a UI flattening, or a different deployed-era projection. It should not be treated as proof that the current checkout persists a plain string in that field. The lifecycle reason itself is still the same decision point: it is chosen in `reconcile.py` before persistence.

## Where `stop_requested` is written

The investigation found two production persistence paths in the Python meeting service. There is also a small `lifecycle/stop.py` helper that sets the in-memory `MeetingRecord.stop_requested` field for its isolated lifecycle fixture; the application factory mounts `stop_router.py` for the real HTTP path, so the helper is not a third database writer in the deployed meeting-api flow.

### 1. User stop route

The user-facing DELETE/stop route writes `stop_requested=True` when a user explicitly stops a bot. That is the legitimate source of the flag.

File:

[stop_router.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/lifecycle/stop_router.py)

The route handles pre-active statuses such as `requested`, `joining`, and `awaiting_admission`. It may also request runtime deletion immediately. Existing tests intentionally preserve this behavior: a real user stop before admission must remain a permanent stopped outcome and must not be retried.

### 2. Service-authority denial

The bot-spawn adapter can also set `stop_requested=True` when a service-authority decision denies a billable active session. This is a policy/system decision, not a human pressing the user stop endpoint.

File:

[adapters.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/bot_spawn/adapters.py)

The current service-authority path applies to continuation/sweep processing for sessions that are already considered `active` or `needs_help`; its decision record is created at admission and its continuation sweep uses `admitted_at`. It moves the row toward `stopping` and records the stop marker for teardown. Under the current listing and admission rules, it cannot normally create the exact `requested (never admitted)` case from a brand-new nonexistent meeting. Calling it a likely source of the four issue examples would be speculation.

The fake adapter mirrors this path:

[fakes.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/bot_spawn/fakes.py)

## Confirmed state-reuse bug fixed on this branch

The meeting continuation flow can reuse a terminal meeting row. Before the patch, the old row’s `stop_requested` value could survive into the new workload. This is a real, reproducible path to the false message, but it requires a continuation/reuse scenario.

That creates this failure sequence:

```text
1. User stops workload A.
2. Meeting row stores stop_requested=True.
3. Workload A becomes terminal.
4. Client continues the meeting.
5. Workload B reuses the same row.
6. Workload B disappears before admission.
7. Reconciler sees stop_requested=True.
8. API reports “stopped by the user,” although the user did not stop B.
```

The patch clears stale terminal metadata in both reopening paths:

- `reopen_meeting` clears `stop_requested`, `completion_reason`, and `failure_stage`.
- The guarded planned-row claim removes an old `stop_requested` value before the replacement workload is started.
- The fake repository follows the same rules so tests model production behavior.

This is deliberately a narrow reset. It does **not** erase every historical or per-run diagnostic in the JSON document, such as `last_error`, `failure_reason`, `error_details`, or accumulated transcripts/recordings. Keeping those records is useful for forensics, but a final implementation should decide explicitly which fields are run-scoped and either clear them on reopen or store them under a per-session/run object. The current patch only guarantees that the prior stop attribution cannot be reused by the replacement workload.

Changed files:

- [adapters.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/bot_spawn/adapters.py)
- [fakes.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/src/meeting_api/bot_spawn/fakes.py)
- [test_continue_meeting.py](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/services/meeting-api/tests/test_continue_meeting.py)

The regression test verifies that a continued meeting does not inherit the prior stop marker and that a replacement workload dying while `awaiting_admission` receives a non-user terminal reason.

## Why that fix is not the complete explanation for #1109

The stale-state bug is real and directly related to the misleading message. However, it cannot by itself explain all examples in the issue:

- The issue lists several different meeting-row IDs.
- The report describes the first API call for those synthetic meetings.
- A normal new meeting request does not reuse a terminal row when there is no active duplicate.
- The issue describes the first API call for the synthetic meetings, which argues against `continue_meeting` unless the hosted caller or an earlier request reused the same row in a way not shown in the report.

Therefore, the current branch should be described as **state-reuse hardening**, not as the proven root-cause fix for every fresh nonexistent-meeting failure.

### Exact proof from the current source

For a brand-new normal spawn, the current flow is:

1. `request_bot` optionally resolves a terminal row only when `continue_meeting=True`.
2. Otherwise `create_meeting_guarded` creates a row with `status="requested"` and the request’s `data`.
3. That new request data contains configuration, webhook, and service-authority metadata, but no `stop_requested` key.
4. The user stop route is the only ordinary pre-active HTTP path that writes `data={"stop_requested": True}`.
5. The service-authority denial writer operates on rows selected as `active` or `needs_help`, then changes them to `stopping`; it is not a fresh `requested`-row path.
6. The runtime reconciler reads `stop_requested` and chooses the message; it does not set the flag for a pre-active row.

So a fresh row reaching the reconciler as `requested` with `stop_requested=True` is evidence of one of the following, not evidence of invalid-meeting detection by itself:

- a stop request raced with or preceded workload teardown;
- a stale/reused row or a planned-row state carried the marker;
- a write came from a deployed code path not present in this checkout;
- the affected row was not actually fresh despite the report describing a first API call;
- the production data was correlated to the wrong workload/meeting row.

Only production row history and runtime logs can select among these possibilities.

## Termination-path comparison

| Path | Meeting stage when it happens | Writes `stop_requested`? | Expected attribution |
|---|---|---:|---|
| Google Meet reports an invalid/nonexistent meeting | usually `joining` or `awaiting_admission` | No | bot failure such as `awaiting_admission_rejected` or `join_failure`; exact browser reason should be preserved |
| User calls the stop endpoint before admission | `requested`, `joining`, or `awaiting_admission` | Yes | `stopped`; user-stop wording is appropriate |
| Service-authority denial | normally an admitted/live session | Yes | system/service-authority stop; it should not be described as a human click |
| Bot process exits by itself | any runtime stage | No | runtime/bot failure or neutral fallback, depending on callback evidence |
| Runtime destroy after a real stop or stale marker | pre-active or live | Existing marker is consumed | exact text depends on whether the marker is valid for this run |
| `continue_meeting` reuses a terminal row | new attempt on an old row | Before this branch, possibly inherited | false user-stop attribution; fixed for the marker on this branch |

The table shows why the issue cannot be closed by changing only Google Meet’s page classifier: the false phrase is selected later, in Python, from persisted stop state. It also shows why changing every runtime death to “meeting not found” would be unsafe.

## Google Meet error classification problem

The Google Meet selector detects explicit page text including:

- `Meeting not found`
- `Can't join the meeting`
- `Unable to join`
- `Access denied`

File:

[selectors.ts](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/modules/join/src/googlemeet/selectors.ts)

However, the admission code groups the generic `error_page` verdict with the rejection path and uses a message equivalent to:

```text
Bot admission was rejected by meeting admin
```

File:

[admission.ts](C:/Users/yarak/Desktop/internship/rtt/vexa-1109-work/core/meetings/modules/join/src/googlemeet/admission.ts)

This is semantically wrong for a nonexistent meeting. A page saying “Meeting not found” is not evidence that a meeting administrator rejected the bot. The join layer should preserve the specific browser diagnosis, for example:

```text
Meeting not found or could not be joined
```

That is a TypeScript-side change, not a Python/FastAPI-only change. It is relevant to the revised issue ask, but it is not the proven generator of the exact “stopped by the user” string.

## Runtime callback information loss

The runtime event schema contains fields such as:

- `exitCode`
- `stopReason`

The meeting-api runtime callback handler receives the event and uses the workload ID, workload state, and event timestamp to reconcile the workload. In the synthetic-terminal path, it does not currently use the runtime’s `exitCode` or `stopReason` to create a more accurate terminal reason.

This creates an information-loss boundary:

```text
Browser detects a join problem
  -> bot/runtime may know a reason
  -> normal bot callback may be missing
  -> runtime destroy callback reaches meeting-api
  -> meeting-api has no browser-level reason
  -> generic synthetic reason is created
```

If the normal bot callback is absent, Python cannot truthfully reconstruct “Meeting not found” from a workload disappearance alone. The reason must either be persisted before teardown or be carried through the runtime callback. The stronger statement is that the runtime metadata is available in the contract, but this handler currently discards it for synthetic attribution; that is information loss, not proof of the reported production path.

## Root-cause assessment

### Confirmed

- The exact false user-stop message is produced by Python lifecycle reconciliation.
- The message is selected from the row-level `stop_requested` boolean.
- A real user stop is one legitimate producer of that boolean.
- Continuation could reuse stale stop state; this branch clears it.
- Google Meet explicitly detects “Meeting not found,” but the admission message is too generic and can incorrectly describe it as an admin rejection.
- Runtime fallback receives runtime state/timestamp but does not currently preserve `exitCode` or `stopReason` in the synthetic lifecycle reason.
- Workload IDs are generated with a fresh connection UUID and are looked up by exact `bot_container_id`; the native placeholder meeting ID is not, by itself, a workload-correlation key.

### Not proven from the available repository evidence

- That every fresh synthetic meeting failure in #1109 was caused by stale `stop_requested` state.
- That the service-authority path generated those four exact `requested (never admitted)` examples.
- That the runtime destroy event alone contains enough information to distinguish a nonexistent meeting from every other pre-admission failure.
- That the current public repository checkout is identical to the hosted build that produced the four examples.
- That “Meeting not found” was actually visible to the browser in those runs; the issue comment asks for that classification, but supplies no browser screenshot/log or lifecycle reason proving it.
- That the latest-session lookup is the cause of these four examples; it is a structural weakness, but the affected issue rows have not been correlated to an overwritten or late workload mapping.

This distinction matters because changing all pre-active failures to “meeting not found” would be incorrect: a bot can fail before admission for many other reasons, including browser startup, authentication, network, permissions, or a genuine user withdrawal.

## Evidence required to prove the production trigger

For each affected meeting row, the decisive evidence would be:

1. The complete `meetings.data` JSON, especially `stop_requested`, `service_authority`, `failure_reason`, and `last_error`.
2. The complete `status_transition` array, including each transition’s source, timestamp, reason, and session/connection ID.
3. `created_at`, `updated_at`, the stored `bot_container_id`, and all `MeetingSession.session_uid` values for that row.
4. The runtime event for the workload, including `state`, `exitCode`, `stopReason`, and `at`.
5. The bot lifecycle callback or bot logs showing whether Google’s error page was detected and whether a normal terminal callback was emitted.
6. API/request audit logs for a DELETE/stop request and service-authority decision logs for the same meeting/workload.
7. The deployed meeting-api and bot image revisions, because this checkout may not be the exact build that produced the issue data.

The most discriminating check is simple: if `stop_requested=true` appears in the row before any user DELETE and the transition trail has no system-authority stop, the flag was either stale/corrupted or written by code not represented here. If a DELETE precedes it, the message is technically consistent with the current implementation even if the caller was an automated test rather than a human.

## Recommended complete implementation

The safest complete fix is layered.

### Layer 1 — Keep the stale-state reset

Retain the current branch changes. They prevent a continued workload from inheriting terminal stop state and preserve the real-user-stop behavior for the original workload.

### Layer 2 — Preserve the origin of a stop request

The persisted lifecycle state should distinguish at least:

- `user`
- `service_authority`
- `system/runtime`

The reconciler should use the source, not only a boolean, when deciding whether to say “stopped by the user.” This is more robust than treating every `stop_requested=True` value as proof of a user action. The minimum safe version is to record a source at every writer and have the user-facing message require `stop_requested_source="user"`; a migration/default policy is needed for legacy rows.

Any schema change must preserve compatibility with existing rows, where a true legacy flag may have no explicit source. Legacy rows should be handled conservatively and should not be reclassified as a user stop unless the service has evidence for that attribution.

### Layer 3 — Preserve Google’s specific join diagnosis

The join module should map an explicit “Meeting not found” page to a specific non-user reason. It should not route every `error_page` verdict through the admin-rejection message.

The preferred data flow is:

```text
Google page diagnosis
  -> structured join failure reason
  -> bot lifecycle callback and/or runtime metadata
  -> meeting-api terminal record
  -> API response
```

### Layer 4 — Improve the runtime fallback

When the normal lifecycle callback is missing, the runtime callback handler should include available `stopReason` and `exitCode` data in the synthetic terminal event. It should still avoid inventing a precise cause when the runtime only reports generic destruction.

The fallback should produce a neutral reason such as:

```text
workload terminated before admission; join reason unavailable
```

unless a structured lower-level reason is available. It must not produce a user attribution from an unverified stale or system-generated marker.

## Tests that should protect the final behavior

The final implementation should include tests for each attribution source:

1. A real user stop before admission remains `completion_reason="stopped"` and keeps the user-stop explanation.
2. A continued meeting clears the previous workload’s stop marker.
3. A service-authority stop is not described as a human user stop.
4. A nonexistent Google Meet is reported as not found/could not be joined when the browser provides that diagnosis.
5. A generic runtime destroy with no lower-level reason receives a neutral fallback message.
6. A runtime destroy carrying a structured join reason preserves that reason.
7. Repeated reconciliation remains idempotent and does not overwrite a more specific terminal reason with a generic one.

The current branch already covers item 2 and verifies the continued-workload regression. It also passes the focused continuation, authority, stop-route, and runtime-destroy tests. The broader suite has two existing lifecycle-seam failures unrelated to the changed files; those failures concern reconciliation timing/escalation behavior.

## Current branch status

No commit, push, or pull request was created.

The working branch is:

```text
codex/issue-1109
```

The only changed source/test files are:

```text
core/meetings/services/meeting-api/src/meeting_api/bot_spawn/adapters.py
core/meetings/services/meeting-api/src/meeting_api/bot_spawn/fakes.py
core/meetings/services/meeting-api/tests/test_continue_meeting.py
```

The investigation document itself is separate from those implementation changes.

## Validation performed

The following checks were completed on the branch:

- Focused continuation tests: **5 passed**.
- Narrow lifecycle/authority/stop/runtime test selection: **43 passed**.
- Python compilation checks for changed Python files: passed.
- Whitespace/error check on the diff: passed.
- Broader meeting-api test run: **177 passed, 2 skipped, 2 unrelated existing failures** in lifecycle-seam timing tests.

## Conclusion

The false message is caused by an attribution decision in the Python lifecycle fallback, not by HTTP response formatting alone. The current branch fixes one concrete, reproducible path: stale `stop_requested` state leaking from an earlier stopped workload into a continued workload.

The deeper investigation does not prove that stale continuation state caused the four fresh examples in #1109. It proves that the current source cannot turn an invalid meeting into `stop_requested=True` without another event or state path. A complete fix should therefore: preserve stop origin, carry runtime terminal evidence into synthetic lifecycle reasons, and preserve the Google Meet join layer’s specific diagnosis. Until production transition history and runtime/bot logs are available, claiming one exact trigger for all four examples would be too strong.
