# counting-fixture — the committed L3.5 audio (issue #560)

Stage-1 counting audio for the wav→words value leg (`deploy/compose/tests/counting_stt_test.py`,
`make -C core/meetings/eval counting-ci`): numbers **1..20**, speakers **A/B** switching every 5
(`silence` scenario), Deepgram Aura voices, linear16 16 kHz WAV — ~284 K total, small enough to
commit (the large regenerable store stays gitignored at `~/vexa-test-rig/fixtures`, see
`../COUNTING-FIXTURES.md`).

- `1-audio/turnNNN.wav` — one WAV per turn (real TTS speech; the leg's STT input)
- `truth.jsonl` — the oracle: per turn `{turn, speaker, numbers, start}`
- `manifest.json` — scenario/N/speakers/cadence + generator provenance

Speaker labels are the ORACLE assignment (ground truth), not diarization — the leg proves
audio→words through local CPU STT and the collector/API path, never diarization quality.

Regenerate (needs `DG_KEY`): the plan/TTS come from `../src/counting_fixture.py`
(`turn_plan(20, "silence", ["A", "B"], 5)` + `tts()` per turn; `truth.jsonl` rows are the plan
with cumulative start times; audio duration = `(bytes − 44) / 32000`).
