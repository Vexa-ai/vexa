# mixed-pipeline fixtures

_meetings/ · module `mixed-pipeline` · committed, offline test material for the mixed (Teams) lane._

Small, byte-stable fixtures that are checked in so offline runs need no live meeting, no keys and no
network. Each subdirectory is one fixture family:

- [`counting/`](counting/): deterministic **1..N counting** meetings (the scripted half), built by
  [`core/meetings/eval/src/counting_fixture.py`](../../../eval/src/counting_fixture.py).

Large or voice-bearing material (TTS audio, STT output) is not kept here. It lives in the gitignored
store `~/vexa-test-rig/fixtures/` (see
[`COUNTING-FIXTURES.md`](../../../eval/COUNTING-FIXTURES.md)).
