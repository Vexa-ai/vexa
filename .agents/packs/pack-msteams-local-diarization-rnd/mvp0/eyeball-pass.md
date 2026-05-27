# MVP0 — YouTube eyeball pass

This is the **Live/human validation gate for MVP0**, scoped per the pack
epic: one YouTube panel video shared via tab-capture; the dashboard must
show live diarized transcript with **plausible** `speaker_0` / `speaker_1`
boundaries. Plausible — not accurate. MVP0 ships an obviously-stub
diarizer (RMS-energy VAD + round-robin labels). Accurate diarization
arrives at MVP1 with the pyannote sidecar.

Fill this in after running the demo.

---

## Validator

- Name: <!-- e.g. dmitry@vexa.ai -->
- Timestamp (UTC): <!-- e.g. 2026-05-27T13:42:00Z -->

## Test session

- YouTube URL: <!-- panel / podcast / debate video -->
- Length watched: <!-- minutes:seconds -->
- Diarizer in use: `vad-round-robin (MVP0 stub, RMS-energy VAD)`
- `TRANSCRIPTION_URL`: <!-- value used, or "(unset — placeholder transcripts)" -->
- `NUM_SPEAKERS`: <!-- default 2 -->
- Harness commit (`git rev-parse --short HEAD` inside the worktree): <!-- e.g. abc1234 -->

## What I checked

- [ ] `/dashboard` populated in real time as the YouTube tab played.
- [ ] Speaker chips alternated `speaker_0` / `speaker_1` at speech-turn boundaries.
- [ ] Each row had a timestamp and either a transcript line or an honest
      placeholder `[transcription service offline …]` line.
- [ ] No crashes, no permanent silence, no "stuck on one speaker forever"
      (occasional sticking on one speaker is expected — it's a stub).

## Verdict

- Overall: <!-- pass / pass-with-notes / fail -->

## Notes

<!--
Free-text. Things to note if relevant:
- Roughly how often speaker labels flipped (should be at every silence break).
- Whether transcript text looked like the actual video audio (if
  transcription was reachable).
- Any UX glitches in the capture page or dashboard.
- Anything that made you doubt the seam architecture, vs. just the stub
  quality (stub quality is expected to be bad).
-->

## Reminder of scope honesty

At MVP0 we are NOT validating diarization correctness. We ARE validating:

1. Tab audio successfully flows browser → harness → diarizer → pipeline → dashboard.
2. The `Diarizer` interface is the right seam for MVP1's pyannote sidecar to plug into.
3. The dashboard is a usable demo surface for stage 2 conversations.

If those three hold, MVP0 is done — regardless of whether the stub's
specific labels look right.
