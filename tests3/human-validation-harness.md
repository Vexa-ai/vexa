# Human Validation Harness

Purpose: canonicalize human validation across develop, stage, and production.

Human validation is not "the human clicks around until they find confidence."
It is a controlled harness:

1. Machine prepares exact URLs, credentials, observer commands, and expected proof IDs.
2. Machine dispatches the bot or action under test.
3. Human performs only the irreducibly human part: admit the bot, speak/listen, judge UI clarity, and notice product discomfort.
4. Machine verifies logs, API state, registry checks, transcript/recording evidence, and cleanup.
5. Machine presents a short verdict to the human: pass, bounce, or inconclusive with exact reason.
6. Human signs only after the verdict and their own sensory judgment agree.

The harness exists because old evidence can be stale. A registry pass on meeting
`10084` does not prove a fresh human-gate meeting `10087`. The validation target
must be the current harness-run artifact unless the checklist explicitly says it
is validating historical playback.

## Core Contract

- The human never has to discover URLs, ports, tokens, meeting IDs, or commands.
- The AI does not mark `approved: true`, sign, or infer approval from chat.
- Every human-visible validation has a machine-owned proof immediately before or after it.
- The verdict names the tested artifact: meeting id, native meeting id, stack URL, image tag, commit, and report file.
- If a live human-walk artifact fails a registry check, the gate bounces even if older registry evidence passed.

## Standard Bot Transcript Harness

Use this shape for any "real bot joins a meeting and transcribes" validation.

Machine prepares:

- dashboard URL;
- gateway URL;
- API token source;
- meeting URL to use;
- bot name containing the gate and stage;
- observer commands for status, transcripts, logs, and containers;
- expected registry check, usually `LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT`.

Machine runs:

```bash
curl -sS -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "meeting_url": "https://meet.google.com/<code>",
    "bot_name": "Vexa <stage> human gate",
    "transcribe_enabled": true,
    "recording_enabled": true,
    "automatic_leave": {
      "no_one_joined_timeout": 300000,
      "everyone_left_timeout": 30000
    }
  }'
```

Human does:

- admit the named bot;
- speak a clear phrase for 30-60 seconds;
- optionally confirm whether TTS or playback sounds correct.

Machine verifies:

```bash
curl -sS -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/id/<meeting_id>"
curl -sS -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/<platform>/<native_id>?meeting_id=<meeting_id>"
STATE=<state-dir> LIVE_BOT_MEETING_ID=<meeting_id> bash tests3/tests/live-bot-transcript-pipeline.sh
docker logs --since 5m <bot-container>
```

Pass requires:

- bot status reached `active`;
- bot detected human audio;
- recording chunks uploaded when recording is enabled;
- transcript segments are non-empty for the same `meeting_id`;
- registry report is `pass`;
- stop/cleanup leaves no running bot container for that meeting.

Bounce examples:

- bot reaches `active` and uploads recording chunks, but transcript segments stay `0`;
- bot logs `TranscriptionClient ... fetch failed`;
- the bot container cannot resolve or reach the configured transcription host;
- human hears no TTS even though API reports success;
- UI shows a misleading success state that machine evidence contradicts.

## Blast-Radius Variants

| Stage | Stack | Human Role | Machine Strictness | Allowed Side Effects | Bounce Target |
|---|---|---|---|---|---|
| `develop-human` | local lite + compose | eyeroll local product path; admit/speak/listen | current local artifact must pass registry checks; older evidence is only background | local meeting rows, local containers, local recordings | `develop-code` |
| `stage-human` | fresh canonical lite + compose + helm | final eyeroll and code-review judgment | same harness, plus validate-report/image-tag/commit consistency | throwaway stage infra only | `develop-code` |
| production validation | production or production-like canary | customer-path confirmation under real blast radius | read-only or narrowly scoped; prefer canary/test account; abort on first customer-risk signal | only pre-approved test account/bot/session | rollback, hotfix, or do-not-release |

## Develop-Human Variant

Develop-human answers: "Is the local product path believable enough to stage?"

Required handoff:

- local dashboard URLs for lite and compose;
- local gateway/docs URLs;
- login identity;
- current release stage and legal next states;
- registry proof summary;
- live harness command outputs for any fresh real-meeting validation.

Human should not be asked to start the bot from UI if the validation target is
transcription correctness. The machine dispatches so it can pin the exact
`meeting_id` and run the registry check against that id.

## Stage-Human Variant

Stage-human answers: "Is the canonical stack safe enough to release?"

Additional requirements:

- validate report is green for the same commit/image tag;
- every URL points at the fresh canonical stack, not the local develop stack;
- `LIVE_BOT_MEETING_ID=<stage meeting id>` is used for live transcript proof;
- code review and product eyeroll remain separate approvals.

No code edits happen in this stage. Any failure bounces to `develop-code`.

## Production Variant

Production validation answers: "Did the shipped/canary path behave for the
smallest safe real-world blast radius?"

Rules:

- use a test account or explicitly approved customer-safe canary;
- do not run broad customer-affecting probes;
- do not issue refunds, sends, deletes, or billing mutations from this harness;
- stop immediately on customer-risk symptoms;
- present the result as operational evidence, not as release authorization by itself.

Production validation can confirm a release or trigger rollback/hotfix, but it
does not replace `stage-human`.

## Verdict Format

Every harness run ends with:

```text
Human validation verdict: <pass|bounce|inconclusive>
Stage: <develop-human|stage-human|production>
Stack: <lite|compose|helm|production>
Artifact: meeting_id=<id>, native_id=<id>, bot=<name>, image_tag=<tag>, git=<sha>
Human observed: <admitted/heard/saw>
Machine observed: <status/chunks/segments/logs/cleanup>
Registry report: <path> <pass|fail>
Decision: <exact next state or no-transition>
```

