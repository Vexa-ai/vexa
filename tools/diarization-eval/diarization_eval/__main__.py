"""Score a named diarizer over WAV/RTTM fixture pairs.

Command (from tools/diarization-eval/):
python -m diarization_eval --fixtures <dir> --candidate <name> [--collar <s>] [--skip-overlap] [--json <path>] [--limit <n>] [--timeout <s>]
Fixtures: <name>.wav (16 kHz mono PCM16) + <name>.rttm; incomplete pairs warn and skip.
Candidates: candidates/<name>/run.sh <input.wav> <output.rttm>, executable, exit 0.
Each candidate writes RTTM with the fixture name; PYTHON and DIAR_REF_RTTM are exported.
Columns: file  DER  miss  fa  conf  ref_spk  hyp_spk  cand_s  audio_s
DER is a fraction; miss/fa/conf are speaker-seconds. ALL accumulates the metric.
JSON contains these columns plus candidate, collar, skip_overlap, peak_rss_mb.
Exit 0: every complete fixture scored; exit 2: any candidate/fixture failure.
`--collar` is pyannote's total collar width, ±collar/2 around every reference boundary.
--timeout limits each candidate (CLI > DIAR_CANDIDATE_TIMEOUT_S > 1800 seconds); timeouts fail and continue.
Model-loading candidates are verified outside the sandbox by the caller.
"""

from . import main

if __name__ == "__main__":
    raise SystemExit(main())
