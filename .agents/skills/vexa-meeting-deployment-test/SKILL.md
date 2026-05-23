---
name: vexa-meeting-deployment-test
description: Run an end-to-end human-in-the-loop Vexa deployment test against live Google Meet or Microsoft Teams meetings. Use when Codex is asked to validate a Vexa deployment by collecting meeting URLs, configuring https://httpbin.org/post webhooks, deploying a listener bot plus N speaker bots from dedicated test users, making speaker bots speak a reusable ground-truth conversation through the voice/TTS API, validating listener transcription and speaker identification quality, stopping bots, checking playback or recording availability, reviewing telemetry and webhook delivery, measuring latency, and producing a test report.
---

# Vexa Meeting Deployment Test

## Purpose

Run a real deployment smoke test that proves bots can join live meetings, multiple dedicated speaker bots can speak a reusable ground-truth conversation, a separate listener bot can transcribe those speakers into the dashboard with useful content accuracy and speaker identification, webhooks deliver, bots stop cleanly, and enough telemetry remains for a credible report.

This skill is intentionally human-in-the-loop. Pause for the required user confirmations instead of pretending to validate visual dashboard state or meeting playback that only the human can see. The human is the ear and eyeball sensor: request concrete observations for the meeting UI, bot names, audio behavior, speaker labels, and playback/artifact state.

The WebSocket connection and live transcript delivery are machine validations. Do not ask the human to confirm that WS connected or that transcript packets moved. The runner must subscribe to the exact current listener meeting id, capture real transcript events in evidence, and report failure when no transcript event is observed. Rendered transcript text is not WS proof by itself because it can come from REST bootstrap; dashboard WS validation needs actual browser WS frame evidence when the dashboard client is in scope.

## Chat Eyeball Checkpoints

When a run reaches a point that needs human eyes or ears, ask in the chat with bullet lists at that moment. Do not rely on hidden terminal prompts as the only human request.

The runner may write a checkpoint file such as `human-eyeball-request.md` or
`playback-eyeball-request.md`. When it does, `cat` that file and deliver the
same bullet block in chat before continuing.

For the post-speech checkpoint, send bullets like:

- Platform/case: `<platform> / <case-id>`
- Dashboard URL: `<dashboard-url>/meetings/<listener-meeting-id>`
- Meeting UI: did the listener bot and both speaker bots appear?
- Bot names: are the speakers visibly named `Maya Chen` and `Leo Santos`?
- Audio: did you hear the speaker bots?
- Voices: did Maya and Leo sound distinct?
- Multilingual: were Spanish, French, and Portuguese checkpoints audible?
- Speaker labels: does the dashboard visibly distinguish Maya and Leo?
- Errors: any denial, mute, duplicate bot, stuck state, empty UI, or visible error?
- Notes: anything else you saw or heard?

For the post-stop playback checkpoint, send bullets like:

- Platform/case: `<platform> / <case-id>`
- Dashboard URL: `<dashboard-url>/meetings/<listener-meeting-id>`
- Artifact: is a recording or transcript artifact visible?
- Playback: can playback or artifact viewing start?
- Match: if playback starts, does it match the meeting audio/content?
- Processing/errors: is it processing, empty, or erroring?
- Notes: anything else visible?

Record the user's answers in the run evidence before scoring the final result.

## Full-Cycle Pass Contract

A run is not a full pass until all of these are addressed for each meeting case:

- Webhook configured before bot deployment, with Vexa-side delivery evidence after the meeting completes. For `httpbin.org/post`, a successful Vexa delivery record is enough; do not claim persistent httpbin receipt storage.
- Listener bot deployed with transcription and recording enabled when supported; speaker bots deployed from distinct test users with voice enabled and transcription/recording disabled when supported.
- Human confirms only the eyeball/ear checks that automation cannot see: bot presence, expected visible names, audible speech, distinct voices, multilingual checkpoint audibility, speaker-label visibility, visible UI errors, and recording/playback surface state. Machine-validated WS connection, transcript movement, timing, order, and latency checks stay in telemetry, WS evidence, and transcript scoring, not in the human prompt.
- WebSocket validation subscribes with the exact current listener meeting id and records at least one real transcript event with text. A mere connection open, rendered transcript text, or `subscribed` acknowledgment is not sufficient.
- Listener transcript fetched and scored against `references/ground-truth-conversation.md`, including content anchors, turn count, speaker-label availability/correctness, and missing/merged/swapped turns.
- Recording or transcript artifact checked after bots stop. If recording/playback is unavailable by product design or still processing, record that exact state instead of silently passing the row.
- Final telemetry report includes bot lifecycle, speak-request timestamps, transcript latency, webhook delivery attempts/statuses, recording/playback observation, and cleanup.
- Cleanup is verified: all listener/speaker bots are stopped or a remaining active resource is explicitly reported.

Any missing item becomes `partial pass` or `fail`; do not mark the meeting case `pass` just because bots joined or TTS requests returned 2xx.

## Guardrails

- Use only user-provided or explicitly approved test meetings. In this workflow, a meeting URL provided by the user for a test run is enough approval to start; do not ask for a separate consent boilerplate unless meeting ownership or authorization is ambiguous.
- Do not print API keys, dashboard secrets, meeting admission secrets, webhook signing secrets, database credentials, or raw private transcripts unless the user explicitly asks for the transcript content.
- Treat `https://httpbin.org/post` as a webhook target, not a persistent receipt store. It returns a response but does not provide later retrieval of delivered requests. Validate webhook delivery from Vexa-side delivery logs, API records, database rows, or service metrics.
- Stop bots on abort, failure, or completion. Report cleanup status even when other checks fail.
- If the target deployment is not already running and the user asked to create one, invoke the relevant Vexa deployment skill first. Otherwise ask for deployment access details.

## Test Topology

Default topology for each meeting case:

- One listener/transcriber bot owned by `test@vexa.ai`. When authenticated meeting/dashboard access is needed, the user will log into this listener account and confirm the transcript there.
- N speaker bots, default N=2, each owned by a distinct dedicated test user. Use one speaker bot per user; do not launch all speakers from the listener user.
- Speaker bots are the only bots that speak. The listener bot is the only transcript source used for pass/fail.
- Speaker bots cannot reliably hear or transcribe themselves, so do not validate speaker-owned transcripts. Validate only the listener bot's transcript for the meeting.
- If the user asks for a different speaker count, use that N.

## Ground Truth Speech

For the default two-speaker run, load `references/ground-truth-conversation.md` and use its 16-turn, approximately three-minute conversation as the reusable speech script.

- Speaker 1 bot speaks only `speaker-1` turns.
- Speaker 2 bot speaks only `speaker-2` turns.
- Preserve the turn order and short pauses between turns.
- Substitute `CASE_ID` and `RUN_ID` placeholders before speaking.
- If the user requests more than two speakers, adapt the script by adding clearly labeled speaker turns for the extra speakers and include that generated ground truth in the report evidence.

## Initial Request

Collect the minimum missing inputs before starting. If no meeting URLs were provided, ask exactly this concise prompt:

`Please provide one or a few meeting URLs from Google Meet or Microsoft Teams.`

Default to asking only for meeting URLs:

- Meeting cases: one or a few Google Meet and/or Microsoft Teams URLs. Generate short labels automatically (`case-a`, `case-b`, ...), unless the user provides labels.
- Speaker count only if the user wants something other than the default 2.
- Deployment access only when it cannot be discovered locally: API base URL, dashboard URL, auth method or token location, and deployment type if known.
- Test user/API-key access only when it cannot be discovered or created: listener user `test@vexa.ai`, plus N dedicated speaker users.
- Human validation contact point only if unclear; otherwise use this thread.
- Time budget only if the user wants something other than the default short smoke test of about 3 minutes per meeting.

Do not ask for a speech plan by default. The default speech source is the speaker bots:

- Enable voice/TTS capability on speaker bot creation when the API supports it.
- Disable transcription/recording on speaker bots when the API supports it, unless needed to make the platform join work.
- Enable transcription/recording on the listener bot.
- After bots join, have speaker bots speak the ground-truth conversation from `references/ground-truth-conversation.md`, one turn at a time.
- If the deployment does not expose bot speech, if bot speech fails, or if the platform blocks audible speaker-bot audio, report that as a product/deployment gap and only then ask the user for a fallback human/YouTube audio source.

If the user provides only part of this, discover what can be discovered from the local checkout, running containers, API docs, and environment files before asking another question.

## Deployment Discovery

Before calling APIs, discover the correct deployment interface from the current repo and running system:

0. Use this skill's scripts as the first reusable harness. `scripts/meeting-tts.sh` runs the default listener plus two speaker TTS test from this skill directory and is intentionally self-contained. Do not use the deprecated `tests3` harness for this workflow.
1. Identify whether this is Lite, Compose, Helm, or another target.
2. Find the bot deployment, stop, transcript, dashboard, and webhook configuration paths from current source, API docs, OpenAPI schemas, README files, Makefiles, or existing scripts.
3. Find how to create or select user API keys for `test@vexa.ai` and dedicated speaker users. Prefer existing admin APIs/helpers. Redact all tokens.
4. Prefer existing CLI/API helpers in the repo over handwritten requests.
5. Verify the dashboard and API are reachable before deploying bots.
6. Record the exact base URLs, endpoints or commands used, versions or image tags when available, and the run ID.

Do not invent endpoints. If the deployment does not expose webhook configuration or bot control APIs, report that as a product/deployment gap and continue with the checks that are possible.

## Test Run

Use a run ID such as `meeting-test-YYYYMMDD-HHMMSS` and assign each meeting a case ID.

1. Configure webhook delivery to `https://httpbin.org/post` for the listener user.
   - Use one listener webhook per run or per meeting, depending on the product model.
   - Include the run ID and case ID in supported webhook metadata or labels.
   - Enable transcript, bot status, recording/playback, and error events when event filtering exists.
   - If signing secrets are supported, use a throwaway secret and keep it redacted.
2. Deploy the listener bot to the provided meeting.
   - Use the `test@vexa.ai` listener user's API key/token.
   - If authenticated join is supported or required, use the authenticated listener session for `test@vexa.ai`. If the user must complete login, pause and ask them to log in as `test@vexa.ai`.
   - Enable `transcribe_enabled=true` and recording if supported. Keep `voice_agent_enabled=false` unless the API requires it for listener operation. Keep camera off unless the user asks for it.
   - Capture request time, bot ID, meeting label, platform, configured webhook, and expected dashboard/transcript location.
   - Watch status until the listener bot is joining, joined/active, denied, failed, or timed out.
3. Deploy N speaker bots to the same meeting.
   - Use one distinct dedicated speaker user/API key per speaker bot.
   - Enable `voice_agent_enabled=true`.
   - Set `transcribe_enabled=false` and `recording_enabled=false` when supported. If unsupported, note the gap, but still validate only listener transcript output.
   - Name or label speakers clearly, e.g. `speaker-1-case-a`, `speaker-2-case-a`.
   - Watch status until each speaker bot is joining, joined/active, denied, failed, or timed out.
   - If a lobby/admission step is needed, tell the user exactly which listener and speaker bots to admit.
4. Make the speaker bots speak in the meeting.
   - Discover the supported speak endpoint and payload from OpenAPI/source before calling it; do not invent payload fields.
   - Prefer `POST /bots/{platform}/{native_meeting_id}/speak` when available.
   - Read `references/ground-truth-conversation.md`.
   - Call the speak endpoint for each scripted turn, alternating speaker bots according to the ground truth script.
   - Use the full scripted turn text with `CASE_ID` and `RUN_ID` substituted.
   - Capture each speak request time, response status, response body shape, and any voice/TTS logs.
   - Start or keep running the machine WebSocket transcript probe against the listener meeting id. It must write `ws-transcript-events.jsonl` and `ws-transcript-summary.json` and must not rely on human confirmation for WS success.
   - Send the post-speech chat checkpoint bullets from "Chat Eyeball Checkpoints" exactly at this moment.
   - Confirm whether all bots appeared, whether speaker names matched the expected names, whether the user heard the speaker bots, whether voices were distinct, whether multilingual checkpoints were audible, whether speaker labels were visible, and whether any bot was denied, muted, duplicated, hidden, or visually stuck.
   - If bot speech is unavailable or inaudible, ask the user for fallback speech: a short human phrase that includes the case label and current minute, or a YouTube tab with audible speech for 60-120 seconds.
5. Give the user listener dashboard URLs for validation.
   - Prefer direct dashboard links to the listener meeting, listener bot, or listener transcript when available.
   - For this dashboard, the default checkpoint URL must be the exact listener
     meeting page: `<dashboard-url>/meetings/<listener_bot_id>`. The root
     dashboard URL is not enough for human checkpoint work.
   - If only the dashboard root exists, provide the root URL plus listener bot ID and case label.
   - Every human-eyeball checkpoint must include the dashboard URL as an explicit bullet in chat.
   - Include speaker-label and visible-error bullets in the same chat checkpoint instead of asking only a one-line terminal question.
   - Ask whether the listener meeting page loads, whether speaker labels visibly distinguish Maya and Leo, and whether webhook/status/recording surfaces show obvious UI errors.
6. Machine-validate listener real-time transcript delivery.
   - The WS probe must connect with the listener token, subscribe to `{platform, native_id, meeting_id}`, and observe at least one transcript event with text.
   - Record the subscribed meeting id, message types, transcript event counts, and validation status in the run evidence.
   - If validating the dashboard client, capture browser WebSocket frames with Playwright/CDP or equivalent network instrumentation: created WS URL, subscribe frame containing the exact `meeting_id`, subscribed ack for the same `meeting_id`, and at least one received `type:"transcript"` frame. Do not count DOM-rendered transcript text as dashboard WS delivery evidence unless the WS frames are also captured.
7. If machine WS transcript validation fails, gather diagnostics before stopping:
   - listener and speaker bot lifecycle state and meeting admission state
   - transcription service health and logs
   - dashboard/websocket/API errors
   - recent transcript segment creation for the listener user's meeting
   - webhook attempts and responses
   - audio-source status according to the human
8. Validate listener transcription and speaker identification quality.
   - Fetch the listener transcript from the deployment API/database.
   - Compare it against `references/ground-truth-conversation.md`.
   - Score content accuracy by expected turns and key anchors present.
   - Score speaker identification by matching each expected turn to the transcript's speaker label when labels are available.
   - Validate turn order and note merged, missing, swapped, or hallucinated turns.
   - If the transcript has no speaker labels, score content separately and mark speaker identification unavailable or failed according to the deployment's expected behavior.
9. Stop all deployed bots.
   - Verify the stop command/API succeeds.
   - Stop speaker bots and listener bot. Verify each leaves the meeting or reaches a stopped terminal state.
10. Refetch post-stop deployment evidence.
   - Fetch the listener meeting/bot record again after stop/finalization delay.
   - Extract webhook delivery attempts, HTTP status, retry count, event types, final meeting status, recording status, recording ID, and playback/master URL fields when present.
   - Treat missing webhook, recording, or final-status data as evidence gaps to report.
11. Ask the user to check playback or recording availability on the listener artifact.
   - Send the post-stop playback checkpoint bullets from "Chat Eyeball Checkpoints" exactly at this moment.
   - Ask only after bots stop whether the artifact is visible, whether playback starts, whether playback audio/content matches the meeting, or whether the UI is processing, empty, or erroring.
   - If the platform recording was never started or the deployment does not offer playback, record that explicitly instead of marking it failed.
12. Produce the final score only after human playback/artifact observations are recorded.
   - Include human confirmation fields, transcript score, webhook summary, recording summary, latency summary, and cleanup status in the final run summary.

## Telemetry Review

Review telemetry after the live portion:

- Bot lifecycle: requested, joining, joined, active, stopping, stopped, failed, denied, or timed out.
- Bot speech: speaker bot identities, speak endpoint used, request timestamps, response statuses, whether the human heard audio when confirmed, and voice/TTS service errors.
- Transcription: listener first segment time, listener segment count, language/model details when visible, transcript update cadence, finalization state, and which expected turns/key anchors appeared.
- Quality scoring: listener content accuracy, speaker identification accuracy, turn-order preservation, missing turns, swapped speaker labels, merged turns, hallucinated text, and any unsupported speaker-label state.
- Dashboard: listener dashboard API or websocket errors, visible URLs supplied, human real-time confirmation timestamps.
- Webhooks: configured target, event types, delivery attempts, HTTP status codes, retry count, response time, last error, payload/event correlation IDs.
- Latency: measure from each speaker bot speak request timestamp to listener transcript creation, dashboard confirmation, and webhook delivery. If bot speech is unavailable and fallback human/YouTube speech is used, report observable proxy latencies and uncertainty.
- Recording/playback: listener artifact human confirmation, deployment artifact status, platform recording status, or reason the check was not applicable.
- Resource health: relevant container/process status, service logs, queue/backlog metrics, CPU/GPU/model loading notes when available.

For `httpbin.org/post`, a 2xx response in Vexa telemetry is enough to validate delivery to the endpoint. Do not claim that httpbin preserved or independently confirmed the event unless a persistent receiver was also used.

## Report

Finish with a concise report containing:

- Overall result: pass, partial pass, or fail.
- Deployment: type, API URL, dashboard URL, version/image tag when available, and run ID.
- Meeting case table: case ID, platform, listener bot ID, speaker bot IDs, dashboard URL, listener join result, speaker join results, speaker speech result, listener transcript confirmation, stop result, listener playback/recording confirmation.
- Quality table: expected turns, matched turns, key anchors matched, speaker labels correct/swapped/missing, turn-order result, content accuracy summary, speaker identification summary.
- Webhook table: target, event types, attempts, successful deliveries, failures/retries, p50/p95 or min/max response latency when available.
- Latency summary: first transcript latency, dashboard confirmation latency, webhook delivery latency, plus uncertainty notes.
- Evidence: commands, API calls, log files, container names, dashboard URLs, and timestamps used for validation.
- Cleanup: bots stopped, temporary webhook configs removed or left in place, and any remaining manual action.
- Issues and recommended next actions, ordered by severity.

Redact secrets and avoid pasting long private transcript text. Include only short transcript snippets when they are necessary evidence and the user has allowed it.
