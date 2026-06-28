# Diarization eval harness

Ported from pack-msteams-diarization-cutover (.agents/rnd/diarization-eval).
Threshold changes to src/services/diarization/* land ONLY with eval numbers
from this harness — no vibes-tuning.

- `fast_sweep.py` — replay recorded embeddings through a faithful Python port
  of OnlineSpeakerClustering and grid-search thresholds (no model inference).
- `score.py` / `compare_score.py` — DER, frame accuracy, boundary timing.
- `turn_gate_test.py` — TurnGate behavior tests.
- `run_eval.sh` — end-to-end gate run.

Corpus: the AMI snippets (~590 MB) are NOT vendored. Default location:
  /home/dima/dev/vexa-pack-pack-msteams-diarization-cutover/.agents/rnd/diarization-eval/
Override with EVAL_CORPUS_DIR.
