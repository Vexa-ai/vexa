# Compose Stack Bot Fix Verification
Date: 2026-03-17 14:25:00
Mode: targeted retest (bot bug fixes)
Image: vexa-bot:dev (built 2026-03-17T13:57:44)

## Summary
- PASS: 3
- FAIL: 0
- DEGRADED: 1
- SURPRISING: 1

## Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Test 1: Google Meet alone-timeout fix | Bot ran 3.5+ minutes with host present, no alone-timeout triggered. Found 2 participant tiles, 1 unique from central list. Audio capture working (3 streams, 3 elements). |
| PASS | Test 2: Teams muted-tracks fix | Bot found 5 media elements with ALL tracks muted=true. Connected streams despite muted tracks. Audio pipeline started. Bot stayed alive (did NOT exit after 30s as before). |
| PASS | Test 3: Teams mock (regression) | Full pipeline works: join -> audio (1 element, unmuted) -> 3 speakers detected -> transcription confirmed for Bob Smith, Alice Johnson, Carol Williams -> Redis publish. |
| DEGRADED | Teams real meeting: lobby visibility | Bot only appears in host lobby when meeting URL includes passcode (?p=...). Without passcode, bot goes through full Teams v2 app redirect and never appears in host's lobby. Meeting ID alone is insufficient. |
| SURPRISING | Teams muted track unmute attempt | Bot tries `track.muted = false` which fails with "Cannot set property muted of #<MediaStreamTrack> which has only a getter". This is harmless (fix still works by accepting muted tracks) but logs a misleading error. Could be cleaned up. |

## Root Cause Chains

### Test 1: Alone-timeout fix verified
Bot enters Google Meet -> finds host participant tile -> participant count = 1 (excludes self from central list) -> alone-timeout NOT triggered because host is present -> bot runs indefinitely until stopped.
Previous behavior: bot would count 0 participants and trigger alone-timeout even with host present.

### Test 2: Muted-tracks fix verified
Bot enters Teams real meeting -> `findMediaElements` finds 5 audio elements -> ALL have `muted=true` tracks -> fix accepts muted tracks (previously filtered them out, found 0, exited) -> connects 5 audio streams -> MediaRecorder starts -> audio routing active on `mainAudio-1101` stream.
Key log evidence: `Track 0: enabled=true, muted=true, label=mainAudio-1101` followed by `Connected audio stream from element X/5`.

### Teams lobby passcode requirement
Without passcode in meeting URL, bot navigates to `teams.live.com/meet/<id>` -> redirects to launcher -> redirects to `teams.live.com/v2/` (full Teams client) -> pre-join times out (45s) -> clicks stale "Join now" -> enters a different meeting context -> host never sees bot in lobby.
With passcode (?p=...), bot navigates correctly -> launcher -> light-meetings -> "Continue on this browser" -> pre-join -> "Join now" -> enters lobby -> host can admit.
Root cause: Teams meeting URLs require the passcode parameter for anonymous (bot) access via the light-meetings flow.

## Bot fix scores (updated)
| Fix | Certainty | Evidence |
|-----|-----------|----------|
| Google Meet alone-timeout | HIGH (95%) | Bot ran 3.5min without false alone-timeout. Host present, bot stayed. |
| Teams muted-tracks | HIGH (95%) | 5 muted tracks connected, pipeline started, no early exit. Mock regression test also passed. |

## What was untested
- Google Meet alone-timeout: Did not test the scenario where the host actually leaves (should trigger timeout correctly)
- Teams transcription: No speech from mic in real meeting, so transcription accuracy with muted tracks is unconfirmed (but audio pipeline is running)
- Teams meeting without lobby (auto-admit): Not tested — all tested meetings had lobby enabled
