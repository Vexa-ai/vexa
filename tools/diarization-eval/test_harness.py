"""Public-entry-point and real subprocess checks using temporary fixture pairs."""

import json
import os
from pathlib import Path
import subprocess
import sys
import wave

import pytest

from diarization_eval import generate_fixtures, read_rttm, score

ROOT = Path(__file__).resolve().parent
KEYS = {"file", "DER", "miss", "fa", "conf", "ref_spk", "hyp_spk", "cand_s", "audio_s",
        "candidate", "collar", "skip_overlap", "peak_rss_mb"}


@pytest.fixture
def fixtures(tmp_path):
    return generate_fixtures(tmp_path / "fixtures")


def run(fixtures, candidate, tmp_path, *options, candidates_dir=None):
    report = tmp_path / "report.json"
    args = ["--fixtures", str(fixtures), "--candidate", candidate, "--json", str(report), *options]
    if candidates_dir is None:
        command = [sys.executable, "-m", "diarization_eval", *args]
    else:
        command = [sys.executable, "-c",
                   "import sys; from diarization_eval import main; "
                   "raise SystemExit(main(sys.argv[2:], candidates_dir=sys.argv[1]))",
                   str(candidates_dir), *args]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10)
    return result, json.loads(report.read_text()) if report.exists() else []


def write_rttm(path, name, turns):
    path.write_text("".join(f"SPEAKER {name} 1 {start} {end-start} <NA> <NA> {label} <NA> <NA>\n"
                            for start, end, label in turns))


def custom_candidate(tmp_path, body):
    directory = tmp_path / "candidates" / "custom"
    directory.mkdir(parents=True)
    script = directory / "run.sh"
    script.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
    script.chmod(0o755)
    return directory.parent


def test_generator(fixtures):
    assert {p.name for p in fixtures.iterdir()} == {
        "two_speakers.wav", "two_speakers.rttm", "three_speakers.wav", "three_speakers.rttm"}
    for path in fixtures.glob("*.wav"):
        with wave.open(str(path), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (16000, 1, 2, 320000)
    expected = {
        "two_speakers": [(0, 10, "A"), (10, 20, "B")],
        "three_speakers": [(0, 6, "A"), (6, 9, "B"), (9, 15, "A"), (15, 20, "C")],
    }
    for name, turns in expected.items():
        actual = [(segment.start, segment.end, label)
                  for segment, _, label in read_rttm(fixtures / f"{name}.rttm", name).itertracks(yield_label=True)]
        assert actual == turns


@pytest.mark.parametrize("candidate", ["ref", "permute"])
def test_oracles_and_json(fixtures, candidate, tmp_path):
    result, rows = run(fixtures, candidate, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].split() == "file DER miss fa conf ref_spk hyp_spk cand_s audio_s".split()
    assert [row["file"] for row in rows] == ["three_speakers", "two_speakers", "ALL"]
    for row in rows:
        assert set(row) == KEYS
        assert row["DER"] == row["miss"] == row["fa"] == row["conf"] == 0
        assert row["ref_spk"] == row["hyp_spk"]
        assert row["candidate"] == candidate and row["collar"] == 0 and row["skip_overlap"] is False
        assert row["cand_s"] > 0
        assert row["peak_rss_mb"] is None or row["peak_rss_mb"] > 0
    assert rows[-1]["ref_spk"] == 5
    assert rows[-1]["audio_s"] == 40
    assert rows[-1]["cand_s"] == sum(row["cand_s"] for row in rows[:-1])


def test_single_confusion(fixtures, tmp_path):
    result, rows = run(fixtures, "single", tmp_path)
    assert result.returncode == 0, result.stderr
    first = next(row for row in rows if row["file"] == "two_speakers")
    assert round(first["DER"], 4) == 0.5000
    assert first["conf"] == 10 and first["miss"] == first["fa"] == 0
    assert first["ref_spk"] == 2 and first["hyp_spk"] == 1


@pytest.mark.parametrize("collar,expected,confusion,total", [(0.0, 0.1000, 2, 20), (2.0, 0.0625, 1, 16), (4.0, 0.0000, 0, 12)])
def test_collar_semantics(fixtures, tmp_path, collar, expected, confusion, total):
    hypothesis_path = tmp_path / "shifted.rttm"
    write_rttm(hypothesis_path, "two_speakers", [(0, 12, "A"), (12, 20, "B")])
    reference = read_rttm(fixtures / "two_speakers.rttm", "two_speakers")
    hypothesis = read_rttm(hypothesis_path, "two_speakers")
    details = score(reference, hypothesis, collar=collar)
    assert round(details["diarization error rate"], 4) == expected
    assert details["confusion"] == confusion and details["total"] == total
    assert details["missed detection"] == details["false alarm"] == 0
    # Exercise the CLI pass-through as well as the public single-file scorer.
    candidates = custom_candidate(tmp_path, 'case "$1" in\n'
                                  '  */two_speakers.wav) printf "SPEAKER two_speakers 1 0 12 <NA> <NA> A <NA> <NA>\\nSPEAKER two_speakers 1 12 8 <NA> <NA> B <NA> <NA>\\n" > "$2" ;;\n'
                                  '  *) cp "$DIAR_REF_RTTM" "$2" ;;\nesac')
    result, rows = run(fixtures, "custom", tmp_path, "--collar", str(collar), candidates_dir=candidates)
    assert result.returncode == 0, result.stderr
    assert round(next(row["DER"] for row in rows if row["file"] == "two_speakers"), 4) == expected


def test_missing_pairs_skip(fixtures, tmp_path):
    (fixtures / "two_speakers.rttm").unlink()
    (fixtures / "orphan.rttm").write_text("")
    result, rows = run(fixtures, "ref", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "WARNING: skipping two_speakers" in result.stderr
    assert "WARNING: skipping orphan" in result.stderr
    assert [row["file"] for row in rows] == ["three_speakers", "ALL"]


def test_failure_preserves_other_rows(fixtures, tmp_path):
    candidates = custom_candidate(tmp_path, 'case "$1" in */three_speakers.wav) echo deliberate-failure >&2; exit 7 ;; esac\ncp "$DIAR_REF_RTTM" "$2"')
    result, rows = run(fixtures, "custom", tmp_path, candidates_dir=candidates)
    assert result.returncode == 2
    assert "candidate exited 7" in result.stderr and "deliberate-failure" in result.stderr
    assert [row["file"] for row in rows] == ["two_speakers", "ALL"]
    assert "two_speakers" in result.stdout and "ALL" in result.stdout
    assert rows[-1]["audio_s"] == 20


@pytest.mark.parametrize("via_env", [False, True])
def test_timeout_continues_sweep(fixtures, tmp_path, monkeypatch, via_env):
    # The CLI must override the environment; both routes must bound the run.
    monkeypatch.setenv("DIAR_CANDIDATE_TIMEOUT_S", "1" if via_env else "30")
    candidates = custom_candidate(
        tmp_path,
        'case "$1" in */three_speakers.wav) sleep 30 ;; esac\ncp "$DIAR_REF_RTTM" "$2"',
    )
    options = [] if via_env else ["--timeout", "1"]
    result, rows = run(fixtures, "custom", tmp_path, *options, candidates_dir=candidates)
    assert result.returncode == 2
    assert "ERROR: three_speakers: candidate exited 2" in result.stderr
    assert "candidate timed out after 1 s" in result.stderr
    assert [row["file"] for row in rows] == ["two_speakers", "ALL"]
    assert "two_speakers" in result.stdout and "ALL" in result.stdout
    assert rows[-1]["DER"] == 0 and rows[-1]["audio_s"] == 20


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_rejected(fixtures, tmp_path, timeout):
    result, rows = run(fixtures, "ref", tmp_path, "--timeout", timeout)
    assert result.returncode == 2
    assert "argument --timeout:" in result.stderr
    assert rows == []


def test_corpus_der_is_not_mean(fixtures, tmp_path):
    with wave.open(str(fixtures / "three_speakers.wav"), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(10 * 16000 * 2))
    write_rttm(fixtures / "three_speakers.rttm", "three_speakers", [(0, 9, "A"), (9, 10, "B")])
    result, rows = run(fixtures, "single", tmp_path)
    assert result.returncode == 0, result.stderr
    assert rows[-1]["DER"] == pytest.approx(11 / 30)
    assert rows[-1]["DER"] != pytest.approx(sum(row["DER"] for row in rows[:-1]) / 2)
    assert rows[-1]["conf"] == 11


def test_overlap_and_miss_fa(tmp_path):
    reference = tmp_path / "ref.rttm"
    hypothesis = tmp_path / "hyp.rttm"
    write_rttm(reference, "overlap", [(0, 10, "A"), (5, 10, "B")])
    write_rttm(hypothesis, "overlap", [(0, 10, "A")])
    ref, hyp = read_rttm(reference, "overlap"), read_rttm(hypothesis, "overlap")
    assert score(ref, hyp)["missed detection"] == 5
    assert score(ref, hyp)["diarization error rate"] == pytest.approx(5 / 15)
    assert score(ref, hyp, skip_overlap=True)["diarization error rate"] == 0
    write_rttm(reference, "overlap", [(0, 10, "A")])
    write_rttm(hypothesis, "overlap", [(2, 12, "X")])
    details = score(read_rttm(reference, "overlap"), read_rttm(hypothesis, "overlap"))
    assert details["missed detection"] == details["false alarm"] == 2
    assert details["confusion"] == 0


def test_limit_and_skip_overlap_flag(fixtures, tmp_path):
    result, rows = run(fixtures, "ref", tmp_path, "--limit", "1", "--skip-overlap")
    assert result.returncode == 0, result.stderr
    assert [row["file"] for row in rows] == ["three_speakers", "ALL"]
    assert all(row["skip_overlap"] for row in rows)


@pytest.mark.parametrize("body", ['printf "SPEAKER foreign 1 0 1 <NA> <NA> A <NA> <NA>\\n" > "$2"', 'exit 0'])
def test_invalid_or_absent_hypothesis_fails(fixtures, tmp_path, body):
    candidates = custom_candidate(tmp_path, body)
    result, rows = run(fixtures, "custom", tmp_path, candidates_dir=candidates)
    assert result.returncode == 2
    assert [row["file"] for row in rows] == ["ALL"]
    assert "ERROR:" in result.stderr
