# v0.12.22 — Teams speaker attribution

Teams turns now carry the speaker's name. The bot reads who is audible from the
WebRTC transport's contributing sources, correlates that against the roster panel
and tile names, and attributes at word granularity rather than per turn.

## Teams

- Speaker names resolve from stable DOM attributes, guarded so a control label, a
  clock or a machine token can never become a name
  ([#1119](https://github.com/Vexa-ai/vexa/issues/1119)).
- The audible-source signal comes from the RTP contributing-source list, so
  attribution follows the transport rather than the UI's animation, and stream
  selection is verified against voice energy.
- Names are applied at word granularity. A name another track has already earned
  is treated as contamination, not as disagreement.
- Only the `mainAudio` mix is transcribed; double-mirrored tracks are deduplicated,
  and a mix that is present but carries no sound is abandoned rather than
  transcribed as silence.
- Unattributed speech is the number we are driving to zero. This release cuts it
  substantially and publishes what is left with an empty speaker rather than a
  guess, so the remaining gap stays visible release over release.

## Transcript quality

- Superseded pending drafts are retracted, and a confirmed turn's dangling tail is
  promoted on close, so dedup no longer loses speech.
- Invented media-artifact text is suppressed, and every suppression is reported.
- Overlap trim, gap reclaim, and a four-second cut on long turns.

## Getting it

Images are published multi-arch (`linux/amd64` + `linux/arm64`) under `:v0.12.22`.
Lite and Docker Compose deployments build and run green at this tag.

## Not claimed

- Zoom: not updated in this release — next target.
- Roughly 4–7% of rows still publish unnamed under heavy crosstalk.

## Credits

Jacob Schooley ([@jbschooley](https://github.com/jbschooley)) — transcript
retract/promote (`6eff8c09`, `ba382219`) and `mainAudio` dedup (`dc740e52`), from
[#1024](https://github.com/Vexa-ai/vexa/pull/1024); also `2db27364`.

Daniel Dormann ([@danieldormann](https://github.com/danieldormann)) — structural
Teams name resolver (`0d0bdbda`), from
[#1121](https://github.com/Vexa-ai/vexa/pull/1121), and the report that opened
[#1119](https://github.com/Vexa-ai/vexa/issues/1119).
