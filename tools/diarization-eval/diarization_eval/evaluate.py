"""Candidate discovery, isolated execution, and accumulated pyannote scoring."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from pyannote.core import Annotation, Segment
from pyannote.metrics.diarization import DiarizationErrorRate

CANDIDATES = Path(__file__).resolve().parents[1] / "candidates"
COLUMNS = "file DER miss fa conf ref_spk hyp_spk cand_s audio_s".split()
COMPONENTS = {"miss": "missed detection", "fa": "false alarm", "conf": "confusion"}


def read_rttm(path, file_name):
    """Parse single-fixture RTTM, preserving simultaneous tracks and rejecting foreign IDs."""
    annotation = Annotation(uri=file_name)
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 10 or fields[0] != "SPEAKER" or fields[1] != file_name or fields[2] != "1":
            raise ValueError(f"{path}:{number}: expected SPEAKER {file_name} 1 and ten RTTM fields")
        start, duration = float(fields[3]), float(fields[4])
        if not all(math.isfinite(v) for v in (start, duration, start + duration)) or start < 0 or duration <= 0:
            raise ValueError(f"{path}:{number}: invalid RTTM time")
        annotation[Segment(start, start + duration), number] = fields[7]
    return annotation


def audio_duration(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError(f"{path}: expected 16 kHz mono PCM16")
        return wav.getnframes() / wav.getframerate()


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return number


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def positive_seconds(value):
    number = nonnegative(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None, *, candidates_dir=None):
    candidates = {p.name: p / "run.sh" for p in Path(candidates_dir or CANDIDATES).iterdir()
                  if p.is_dir() and (p / "run.sh").is_file()}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--candidate", required=True, choices=sorted(candidates))
    parser.add_argument("--collar", default=0.0, type=nonnegative,
                        help="pyannote total collar width (±collar/2 per reference boundary)")
    parser.add_argument("--skip-overlap", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--limit", type=positive)
    parser.add_argument("--timeout", type=positive_seconds,
                        default=os.environ.get("DIAR_CANDIDATE_TIMEOUT_S", "1800"),
                        help="candidate wall-time limit in seconds (env DIAR_CANDIDATE_TIMEOUT_S, default 1800)")
    args = parser.parse_args(argv)
    if not args.fixtures.is_dir():
        parser.error(f"fixture directory does not exist: {args.fixtures}")
    fixtures = args.fixtures.resolve()
    pairs = []
    for name in sorted({p.stem for p in fixtures.iterdir() if p.suffix in {".wav", ".rttm"}}):
        wav, ref = fixtures / f"{name}.wav", fixtures / f"{name}.rttm"
        if not wav.is_file() or not ref.is_file():
            print(f"WARNING: skipping {name}: missing WAV or RTTM", file=sys.stderr)
        else:
            pairs.append((name, wav, ref))
    pairs = pairs[:args.limit]
    metric = DiarizationErrorRate(collar=args.collar, skip_overlap=args.skip_overlap)
    metadata = {"candidate": args.candidate, "collar": args.collar, "skip_overlap": args.skip_overlap}
    rows = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="diarization-eval-") as temporary:
        scratch = Path(temporary)
        for name, wav, reference_path in pairs:
            hypothesis_path = scratch / "hypothesis.rttm"
            report_path = scratch / "usage.json"
            hypothesis_path.unlink(missing_ok=True)
            report_path.unlink(missing_ok=True)
            try:
                duration = audio_duration(wav)
                reference = read_rttm(reference_path, name)
                env = {**os.environ, "PYTHON": sys.executable, "DIAR_REF_RTTM": str(reference_path),
                       "PYTHONDONTWRITEBYTECODE": "1", "DIAR_CANDIDATE_TIMEOUT_S": str(args.timeout)}
                with (scratch / "candidate.log").open("w+") as log:
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).with_name("runner.py")),
                         str(candidates[args.candidate]), str(wav), str(hypothesis_path), str(report_path),
                         str(args.timeout)],
                        env=env, stdout=log, stderr=log, check=False,
                    )
                    usage = json.loads(report_path.read_text()) if report_path.exists() else {}
                    if result.returncode or usage.get("returncode", 2):
                        log.seek(0, 2)
                        log.seek(max(0, log.tell() - 4000))
                        raise ValueError(f"candidate exited {usage.get('returncode', result.returncode)}: {log.read().strip()}")
                hypothesis = read_rttm(hypothesis_path, name)
                details = metric(reference, hypothesis, detailed=True)
                rows.append({"file": name, "DER": details["diarization error rate"],
                             **{key: details[value] for key, value in COMPONENTS.items()},
                             "ref_spk": len(reference.labels()), "hyp_spk": len(hypothesis.labels()),
                             "cand_s": usage["cand_s"], "audio_s": duration,
                             **metadata, "peak_rss_mb": usage["peak_rss_mb"]})
            except (OSError, ValueError, wave.Error, EOFError) as error:
                failed = True
                print(f"ERROR: {name}: {error}", file=sys.stderr)
    total = {"file": "ALL", "DER": abs(metric) if rows else 0.0,
             **{key: sum(row[key] for row in rows) for key in COLUMNS[2:]}, **metadata,
             "peak_rss_mb": max((row["peak_rss_mb"] for row in rows if row["peak_rss_mb"] is not None), default=None)}
    rows.append(total)
    print("  ".join(COLUMNS))
    for row in rows:
        print("  ".join(f"{row[key]:.4f}" if key in {"DER", "miss", "fa", "conf", "cand_s", "audio_s"}
                        else str(row[key]) for key in COLUMNS))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n")
    return 2 if failed else 0
