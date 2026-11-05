# counting fixtures (mixed lane)

_The committed scripted half of the counting fixtures for the mixed (Teams) lane._

A counting meeting has speakers saying **1, 2, 3, …** and switching at known boundaries, which makes
the oracle trivial: a missing number is a drop, a repeated one is a duplicate, and a wrong speaker is a
mislabel. Each `<platform>-<scenario>-1to<N>/` directory has:

| file | what it is |
|---|---|
| `truth.jsonl` | one row per number: `n`, speaker label + name, Teams CSRC, `t0_ms`/`t1_ms`, turn |
| `hints.jsonl` | the platform evidence timeline: CSRC activity edges plus dom-outline-shaped speaker hints |
| `manifest.json` | the build arguments, timing model, speakers, and the sha256 of both `.jsonl` files |

The fixtures are built offline, with no keys, by
[`counting_fixture.py`](../../../../eval/src/counting_fixture.py) `--lane mixed`, and rebuilding with
the same arguments produces the same bytes. The `--audio` half (TTS/STT) is store-only and is never
committed. See [`COUNTING-FIXTURES.md`](../../../../eval/COUNTING-FIXTURES.md).
