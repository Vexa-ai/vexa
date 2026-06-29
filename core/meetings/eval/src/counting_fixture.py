#!/usr/bin/env python3
"""counting_fixture — build a DETERMINISTIC 1..N counting fixture, offline, at every stage.

Why: counting is the perfect oracle — known content at every position. We make speakers count 1..N,
switching at scenario-defined boundaries, so the fixture is fully deterministic AND speaker-attributed
(any drop/dup/misattribution/scramble is caught). Offline path (no live meeting, no /speak):

  stage 1  audio   — Deepgram TTS per turn  → <store>/<scenario>/1-audio/turN.wav
  stage 2  STT     — transcription.vexa.ai  → <store>/<scenario>/2-stt.jsonl  (verbose_json per turn)
  stage 3  segments— transcript.v1 segments → <store>/<scenario>/3-segments.jsonl  (speaker-attributed)
  truth/manifest   — the oracle (who said which numbers, when) + run metadata

Stages 4–6 (collector → watcher → copilot) are driven from 3-segments by the local pipeline / fake bot.

Env (from ~/vexa-test-rig/secrets.env): DG_KEY (Deepgram), and the STT is the internal unlimited
transcription.vexa.ai. Usage:
  python counting_fixture.py --scenario silence --n 30 --store ~/vexa-test-rig/fixtures
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DG_TTS = "https://api.deepgram.com/v1/speak"
# STT endpoint is configurable; point VEXA_TX_URL at any OpenAI-compatible /audio/transcriptions.
STT = os.environ.get("VEXA_TX_URL", "http://127.0.0.1:18056/v1/audio/transcriptions")
# A few Deepgram Aura voices, one per speaker (matches the eval roster).
VOICES = {"A": "aura-asteria-en", "B": "aura-orion-en", "V": "aura-luna-en", "C": "aura-stella-en"}
_W = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
      "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
      "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
      "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
      "hundred": 100}


def turn_plan(n: int, scenario: str, speakers: list[str], cadence: int):
    """Partition 1..N into TURNS (speaker, numbers, gap_before_sec) per scenario.
    The switch behaviour is the knob each scenario sets — same 1..N oracle throughout."""
    plan, k, turn = [], 1, 0
    cad = {"dynamic": 2, "solo": n, "continuation": cadence}.get(scenario, cadence)
    gap = {"silence": 1.2, "overlap": -0.4, "dynamic": 0.05, "continuation": 0.3, "solo": 0.0}.get(scenario, 0.8)
    while k <= n:
        m = min(cad, n - k + 1)
        spk = speakers[0] if scenario == "solo" else speakers[turn % len(speakers)]
        plan.append({"speaker": spk, "numbers": list(range(k, k + m)),
                     "gap_before": 0.0 if turn == 0 else gap})
        k += m
        turn += 1
    return plan


def _http(url, data=None, headers=None, method="GET"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def tts(text: str, voice: str) -> bytes:
    url = f"{DG_TTS}?model={voice}&encoding=linear16&sample_rate=16000&container=wav"
    return _http(url, data=json.dumps({"text": text}).encode(),
                 headers={"Authorization": f"Token {os.environ['DG_KEY']}", "Content-Type": "application/json"},
                 method="POST")


def stt(wav: bytes) -> dict:
    boundary = "----countfix"
    parts = []
    for name, val, extra in [("file", wav, b'; filename="a.wav"\r\nContent-Type: audio/wav')]:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"".encode() + extra + b"\r\n\r\n" + val + b"\r\n")
    for name, val in [("model", "whisper-1"), ("response_format", "verbose_json")]:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    key = os.environ.get("TX_KEY") or os.environ.get("TRANSCRIPTION_SERVICE_TOKEN")
    if not key:
        raise SystemExit("set TX_KEY (transcription.vexa.ai STT token) — see ~/vexa-test-rig/secrets.env")
    raw = _http(STT, data=body, headers={"Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    return json.loads(raw)


def nums_in(text: str) -> list[int]:
    out = []
    for tok in re.findall(r"\d+|[a-z]+", text.lower()):
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _W:
            out.append(_W[tok])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="silence",
                    choices=["silence", "overlap", "dynamic", "continuation", "solo"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--cadence", type=int, default=5)
    ap.add_argument("--speakers", default="A,B")
    ap.add_argument("--store", default=os.path.expanduser("~/vexa-test-rig/fixtures"))
    a = ap.parse_args()

    speakers = a.speakers.split(",")
    out = Path(a.store) / "google_meet" / f"count-{a.scenario}-1to{a.n}"
    (out / "1-audio").mkdir(parents=True, exist_ok=True)
    plan = turn_plan(a.n, a.scenario, speakers, a.cadence)
    print(f"· {a.scenario}: {a.n} numbers, {len(plan)} turns, speakers={speakers} → {out}")

    stt_f = (out / "2-stt.jsonl").open("w")
    seg_f = (out / "3-segments.jsonl").open("w")
    truth_f = (out / "truth.jsonl").open("w")
    t = 0.0
    got: list[int] = []
    for i, turn in enumerate(plan):
        words = " ".join(_say(k) for k in turn["numbers"])
        wav = tts(words, VOICES.get(turn["speaker"], "aura-asteria-en"))
        (out / "1-audio" / f"turn{i:03d}.wav").write_bytes(wav)
        v = stt(wav)
        text = (v.get("text") or "").strip()
        dur = float(v.get("duration") or 1.0)
        t += turn["gap_before"]
        stt_f.write(json.dumps({"turn": i, "speaker": turn["speaker"], "expect": turn["numbers"],
                                "text": text, "duration": dur}) + "\n")
        seg_f.write(json.dumps({"segment_id": f"t{i}", "speaker": turn["speaker"], "text": text,
                                "start": round(t, 2), "end": round(t + dur, 2), "completed": True,
                                "language": "en"}) + "\n")
        truth_f.write(json.dumps({"turn": i, "speaker": turn["speaker"], "numbers": turn["numbers"],
                                  "start": round(t, 2)}) + "\n")
        got += nums_in(text)
        t += dur
        print(f"  turn {i:02d} [{turn['speaker']}] expect {turn['numbers'][0]}..{turn['numbers'][-1]}  STT: {text[:48]!r}")
    for f in (stt_f, seg_f, truth_f):
        f.close()

    # ── Oracle: every number 1..N present exactly once, in order ──
    missing = [k for k in range(1, a.n + 1) if k not in got]
    dupes = sorted({k for k in got if got.count(k) > 1})
    in_order = got == sorted(got)
    manifest = {"scenario": a.scenario, "n": a.n, "speakers": speakers, "turns": len(plan),
                "platform": "google_meet", "oracle": {"missing": missing, "dupes": dupes,
                "in_order": in_order, "stt_recall": round(1 - len(missing) / a.n, 3)}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n· ORACLE: missing={missing or 'none'} dupes={dupes or 'none'} in_order={in_order} "
          f"recall={manifest['oracle']['stt_recall']}")
    print(f"· wrote stages 1-3 + truth + manifest → {out}")
    return 0 if not missing and not dupes and in_order else 1


def _say(k: int) -> str:
    return str(k)  # digits TTS cleanly in Aura and round-trips through STT


if __name__ == "__main__":
    sys.exit(main())
