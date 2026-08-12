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
- Closed captions are no longer switched on automatically.
- Where nothing can name a speaker, the row publishes with an empty speaker rather
  than a claim.

## Transcript quality

- Superseded pending drafts are retracted, and a confirmed turn's dangling tail is
  promoted on close, so dedup no longer loses speech.
- Invented media-artifact text is suppressed, and every suppression is reported.
- Overlap trim, gap reclaim, and a four-second cut on long turns.

## Capture signal

A bot can tape the transport and DOM signal for a meeting and store it alongside the
recording — on by default, with a per-deployment kill switch and bounded retention.

## Not claimed

- Zoom is unchanged by this release.
- Roughly 4–7% of rows still publish unnamed under heavy crosstalk.
- The closed-caption path is retained behind a flag; it is not the primary
  attribution source.

## Credits

Jacob Schooley ([@jbschooley](https://github.com/jbschooley)) — transcript
retract/promote and `mainAudio` dedup
([#1024](https://github.com/Vexa-ai/vexa/pull/1024)).

Daniel Dormann ([@danieldormann](https://github.com/danieldormann)) — structural
Teams name resolver ([#1121](https://github.com/Vexa-ai/vexa/pull/1121)), and the
report that opened [#1119](https://github.com/Vexa-ai/vexa/issues/1119).

Review stack: [#1123](https://github.com/Vexa-ai/vexa/pull/1123) ·
[#1124](https://github.com/Vexa-ai/vexa/pull/1124) ·
[#1125](https://github.com/Vexa-ai/vexa/pull/1125) ·
[#1126](https://github.com/Vexa-ai/vexa/pull/1126) ·
[#1127](https://github.com/Vexa-ai/vexa/pull/1127) ·
[#1128](https://github.com/Vexa-ai/vexa/pull/1128).
