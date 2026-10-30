# teams-solo-1to100

_Counting fixture · mixed lane · Teams · `solo` scenario · numbers 1..100._

One speaker (`A` = Alice Adams, CSRC 101) counts 1..100 in 4 turns of 25 numbers (600 ms per number,
800 ms gap between turns). CSRC edges lead the speech by 150 ms, and dom-outline hints lag it by
400 ms. This is the no-switch control for the mixed (Teams) lane.

Files: `truth.jsonl` (100 rows, the oracle), `hints.jsonl` (CSRC + hint timeline), `manifest.json`
(arguments, timing, sha256 of both `.jsonl` files). Rebuild it byte-for-byte with:

```bash
python3 core/meetings/eval/src/counting_fixture.py --lane mixed --platform teams --scenario solo \
  --n 100 --turn 25 --out core/meetings/modules/mixed-pipeline/fixtures/counting/teams-solo-1to100
```
