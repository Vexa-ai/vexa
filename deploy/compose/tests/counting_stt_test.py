"""counting_stt_test — the L3.5 wav→words value leg (#560). OPT-IN via COUNTING_STT=1.

Proves: committed counting-fixture audio → REAL local CPU whisper (the self-hosted transcription
service from deploy/transcription/docker-compose.cpu.yml) → transcript.v1 segments → the REAL
collector (transcription_segments stream) → the words are served by GET /transcripts through the
gateway. The 1..N counting oracle attributes every drop to its stage:

  stage 2   STT        — each wav POSTed to /v1/audio/transcriptions on the CPU whisper LB
  stage 3   segments   — transcript.v1 built from the STT text + GROUND-TRUTH speakers (the oracle
                         assignment, per core/meetings/eval/COUNTING-FIXTURES.md — diarization
                         quality is out of scope)
  stage 4-5 collector  — XADD → meeting-api consumer → API-served segments

Scope (honest): proves fixture audio → words through LOCAL CPU STT and the real collector/API
path. It does NOT claim real-Meet admission (L5), hosted-STT capacity, or diarization quality.

The negative control stops the whisper worker mid-run and asserts the leg goes RED with a
stage-2-attributed failure — the leg discriminates, it doesn't just pass.

Fixture: core/meetings/eval/counting-fixture (committed; ~284K, 4 turns, numbers 1..20,
speakers A/B switching every 5). Regeneration: counting_fixture.py turn_plan/tts (needs DG_KEY).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

from conftest import http, requires_docker, _free_tcp_port
from stack_test import _create_user, _mint_token, _insert_meeting

COUNTING_STT = os.getenv("COUNTING_STT") == "1"
pytestmark = [requires_docker,
              pytest.mark.skipif(not COUNTING_STT, reason="L3.5 counting leg is opt-in (set COUNTING_STT=1)")]

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "core" / "meetings" / "eval" / "counting-fixture"
STT_COMPOSE = REPO / "deploy" / "transcription" / "docker-compose.cpu.yml"
STT_PROJECT = os.getenv("COUNTING_STT_PROJECT", "vexa-counting-stt")
# `tiny` fits the ≤15-min CI budget on a standard runner; the compose default (`small`) is for
# keeping pace with live meetings, which this offline leg does not need.
STT_MODEL = os.getenv("COUNTING_STT_MODEL", "tiny")

_W = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}


def nums_in(text: str) -> list[int]:
    out = []
    for tok in re.findall(r"\d+|[a-z]+", str(text).lower()):
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _W:
            out.append(_W[tok])
    return out


# ── the local CPU whisper (module fixture) ───────────────────────────────────────────────────────

def _stt_compose(*args: str, port: str, check: bool = True, timeout: int = 1800):
    env = {**os.environ, "MODEL_SIZE": STT_MODEL, "TRANSCRIPTION_LB_PORT": port,
           "IMAGE_TAG": os.getenv("IMAGE_TAG", "dev")}
    r = subprocess.run(["docker", "compose", "-p", STT_PROJECT, "-f", str(STT_COMPOSE), *args],
                       capture_output=True, text=True, env=env, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"stt compose {' '.join(args)} failed:\n{(r.stdout or '')[-2000:]}\n{(r.stderr or '')[-2000:]}")
    return r


class WhisperCpu:
    def __init__(self, port: str):
        self.port = port
        self.url = f"http://127.0.0.1:{port}/v1/audio/transcriptions"

    def stop_worker(self) -> None:
        """The negative-control lever: kill the STT worker mid-run (the LB stays up and 502s)."""
        _stt_compose("stop", "transcription-worker-1", port=self.port)


@pytest.fixture(scope="module")
def whisper_cpu():
    port = os.getenv("COUNTING_STT_PORT") or _free_tcp_port()
    _stt_compose("up", "-d", "--build", port=port)
    w = WhisperCpu(port)
    try:
        # First boot downloads the model into the volume — bound the poll generously, poll fast.
        deadline = time.time() + 600
        code, body = 0, None
        while time.time() < deadline:
            code, body = http("GET", f"http://127.0.0.1:{port}/health", timeout=5)
            if code == 200:
                break
            time.sleep(3)
        assert code == 200, f"CPU whisper never became healthy: {code} {body}"
        print(f"\n[counting/stt] CPU whisper up (model={STT_MODEL}) on :{port}")
        yield w
    finally:
        _stt_compose("down", "-v", port=port, check=False)


# ── stage 2: wav → words through the local STT ───────────────────────────────────────────────────

def stt_transcribe(url: str, wav: bytes) -> str:
    """POST one wav to the OpenAI-compatible endpoint → the transcribed text. Raises on any
    non-200 (the caller attributes that to stage 2)."""
    boundary = "----counting" + uuid.uuid4().hex
    out = bytearray()
    out += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="turn.wav"\r\n'.encode()
    out += b"Content-Type: audio/wav\r\n\r\n" + wav + b"\r\n"
    for k, v in (("model", "whisper-1"), ("response_format", "verbose_json"), ("language", "en")):
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=bytes(out), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        doc = json.loads(resp.read())
    return (doc.get("text") or "").strip()


def load_truth() -> list[dict]:
    return [json.loads(l) for l in (FIXTURE / "truth.jsonl").read_text().splitlines() if l.strip()]


def run_stage2(stt_url: str, turns: list[dict]) -> tuple[list[dict], dict]:
    """Transcribe every committed wav. Returns (per-turn results, stage-2 verdict). A transport
    failure (worker down) is recorded — not raised — so the verdict carries the attribution."""
    results, n = [], max(x for t in turns for x in t["numbers"])
    for t in turns:
        wav = (FIXTURE / "1-audio" / f"turn{t['turn']:03d}.wav").read_bytes()
        try:
            text = stt_transcribe(stt_url, wav)
            results.append({**t, "text": text, "got": nums_in(text), "error": None})
        except Exception as e:  # the LB 502s / connection refused when the worker is down
            results.append({**t, "text": "", "got": [], "error": str(e)[:200]})
    got = [k for r in results for k in r["got"]]
    verdict = {"stage": "stage-2 STT",
               "missing": [k for k in range(1, n + 1) if k not in got],
               "errors": [r["error"] for r in results if r["error"]],
               "n": n}
    verdict["red"] = bool(verdict["missing"] or verdict["errors"])
    return results, verdict


# ── the leg (A1/A2): wav → local CPU STT → collector → API-served words ─────────────────────────

def test_01_wav_to_api_served_words(stack, whisper_cpu):
    turns = load_truth()
    n = max(x for t in turns for x in t["numbers"])

    # stage 2 — real CPU STT on the committed audio.
    results, s2 = run_stage2(whisper_cpu.url, turns)
    for r in results:
        print(f"  turn {r['turn']} [{r['speaker']}] expect {r['numbers']} → STT {r['text']!r}")
    assert not s2["red"], (
        f"RED (attributed: {s2['stage']}): missing {s2['missing']} of 1..{n}; errors={s2['errors']} "
        f"— the local CPU whisper (model={STT_MODEL}) did not return every number")

    # stage 3 — transcript.v1 segments, GROUND-TRUTH speakers (the oracle assignment).
    segments = [{"segment_id": f"count-{r['turn']}", "start": float(r["start"]),
                 "end": float(r["start"]) + 3.0, "text": r["text"], "language": "en",
                 "speaker": r["speaker"], "completed": True} for r in results]

    # stage 4-5 — the real collector: an owned meeting, XADD to transcription_segments, then the
    # words must come back API-SERVED through the gateway.
    user_id = _create_user(stack)
    token = _mint_token(stack, user_id, "bot,tx")
    platform, native_id = "google_meet", f"count-{uuid.uuid4().hex[:8]}"
    meeting_id, _session = _insert_meeting(stack, user_id, platform, native_id)
    for seg in segments:
        payload = json.dumps({"type": "transcription", "meeting_id": meeting_id, "segments": [seg]})
        stack.redis_cli("XADD", "transcription_segments", "*", "payload", payload)

    api_segs, deadline = [], time.time() + 45
    while time.time() < deadline:
        code, doc = http("GET", f"{stack.gateway}/transcripts/{platform}/{native_id}",
                         headers={"x-api-key": token})
        if code == 200 and isinstance(doc, dict) and len(doc.get("segments") or []) >= len(segments):
            api_segs = doc["segments"]
            break
        time.sleep(2)
    assert api_segs, f"collector never served the {len(segments)} segments via GET /transcripts"

    # oracle — every number 1..N in the API-served words; a miss here (with stage 2 green) is a
    # DOWNSTREAM (stage 4-5) drop, attributed as such.
    got = [k for s in api_segs for k in nums_in(s.get("text", ""))]
    missing = [k for k in range(1, n + 1) if k not in got]
    assert not missing, (
        f"RED (attributed: stage 4-5 collector/API): {missing} of 1..{n} present after STT "
        f"but absent from the API-served segments")

    # speaker boundaries at the declared switch points: each served segment carries its truth
    # speaker, and the numbers it serves belong to that speaker's truth turn.
    truth_by_id = {f"count-{t['turn']}": t for t in turns}
    ordered = sorted(api_segs, key=lambda s: float(s.get("start") or 0))
    for s in ordered:
        t = truth_by_id.get(s.get("segment_id")) or min(
            turns, key=lambda t: abs(float(t["start"]) - float(s.get("start") or 0)))
        assert s.get("speaker") == t["speaker"], (
            f"speaker boundary violated: segment at {s.get('start')} served speaker "
            f"{s.get('speaker')!r}, truth says {t['speaker']!r}")
        stray = [k for k in nums_in(s.get("text", "")) if k not in t["numbers"]]
        assert not stray, f"numbers {stray} served under speaker {t['speaker']} but belong to another turn"

    print(f"\n[counting/L3.5] GREEN: 1..{n} wav→(local CPU whisper {STT_MODEL})→collector→API-served, "
          f"speakers at truth boundaries. Scope: proves audio→words through local CPU STT; does NOT "
          f"claim real-Meet admission or hosted-STT capacity.")


# ── negative control (A3): STT stopped mid-run ⇒ RED, attributed to stage 2 ──────────────────────

def test_02_negative_control_stt_down_mid_run(stack, whisper_cpu):
    turns = load_truth()
    # First turn transcribes (the run is genuinely mid-flight)…
    first = stt_transcribe(whisper_cpu.url, (FIXTURE / "1-audio" / "turn000.wav").read_bytes())
    assert nums_in(first), f"pre-condition: the first turn must transcribe before the kill ({first!r})"
    # …then the worker dies.
    whisper_cpu.stop_worker()
    _, verdict = run_stage2(whisper_cpu.url, turns)
    assert verdict["red"], "leg stayed GREEN with the STT worker stopped — it does not discriminate"
    assert verdict["stage"] == "stage-2 STT" and (verdict["errors"] or verdict["missing"]), verdict
    print(f"\n[counting/negative] STT stopped mid-run → RED attributed to {verdict['stage']} "
          f"(missing={verdict['missing'][:6]}…, {len(verdict['errors'])} transport error(s)) — "
          f"the leg discriminates.")
