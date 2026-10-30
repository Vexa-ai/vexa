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

Mixed lane (Teams): the SCRIPTED half is offline, needs no key, and is byte-stable on rebuild:
  python counting_fixture.py --lane mixed --platform teams --scenario silence --n 500 --turn 25 \
      --out core/meetings/modules/mixed-pipeline/fixtures/counting/teams-1to500
  → truth.jsonl  {n, speaker, label, csrc, t0_ms, t1_ms, turn}   (one row per number — the oracle)
    hints.jsonl  {type:'csrc',t,csrc,active} + {type:'hint',t,name,isEnd}  (Teams dom-outline-shaped WHO)
    manifest.json (args, seed, names, csrcs, timing, sha256 of truth + hints)
  Add --audio for the store-only AUDIO half (TTS per turn → 1-audio/, 2-stt.jsonl, truth-audio.jsonl
  with times rescaled to the real WAV durations) under --store/teams/count-<scenario>-1to<n>.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import wave
import urllib.request
from pathlib import Path

DG_TTS = "https://api.deepgram.com/v1/speak"
# STT endpoint is configurable; point VEXA_TX_URL at any OpenAI-compatible /audio/transcriptions.
# VEXA_TX_MODEL (or TRANSCRIPTION_MODEL) picks the model id for backends that validate it.
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
    model = os.environ.get("VEXA_TX_MODEL") or os.environ.get("TRANSCRIPTION_MODEL") or "whisper-1"
    for name, val in [("model", model), ("response_format", "verbose_json")]:
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


# ── Mixed lane (Teams): one mixed stream + CSRC routing + dom-outline names ──────────────────────
MIXED_NAMES = {"A": "Alice Adams", "B": "Bob Brown", "C": "Carol Chen", "V": "Vera Vance"}
MIXED_CSRC = {"A": 101, "B": 102, "C": 103, "V": 104}
MIXED_GAP_MS = {"silence": 800, "overlap": -400, "dynamic": 50, "continuation": 300, "solo": 800}
MIXED_CADENCE = {"dynamic": 2}
NUM_MS = 600          # synthetic time per spoken number
START_MS = 1000       # first word; leaves room for the CSRC lead
CSRC_LEAD_MS = 150    # CSRC turns on this long BEFORE the first word (and off this long after the last)
HINT_LAG_MS = 400     # dom-outline lights up this long AFTER the first word (and clears after the last)


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows).encode()


def mixed_script(n: int, scenario: str, speakers: list[str], cadence: int, gap_ms: int,
                 seed: int, jitter_ms: int):
    """Deterministic truth + hint timeline for the mixed lane. Pure function of its args."""
    rng = random.Random(seed)
    cad = MIXED_CADENCE.get(scenario, cadence)
    truth, hints, t, k, turn = [], [], START_MS, 1, 0
    while k <= n:
        m = min(cad, n - k + 1)
        label = speakers[0] if scenario == "solo" else speakers[turn % len(speakers)]
        name, csrc = MIXED_NAMES.get(label, label), MIXED_CSRC.get(label, 100 + turn % 16)
        if turn:
            t += gap_ms
        t0 = t
        for i in range(m):
            truth.append({"n": k + i, "speaker": name, "label": label, "csrc": csrc, "turn": turn,
                          "t0_ms": t0 + i * NUM_MS, "t1_ms": t0 + (i + 1) * NUM_MS})
        t1 = t0 + m * NUM_MS
        j = [rng.randint(-jitter_ms, jitter_ms) for _ in range(4)]
        hints += [{"type": "csrc", "t": t0 - CSRC_LEAD_MS + j[0], "csrc": csrc, "active": True},
                  {"type": "hint", "t": t0 + HINT_LAG_MS + j[1], "name": name, "isEnd": False},
                  {"type": "csrc", "t": t1 + CSRC_LEAD_MS + j[2], "csrc": csrc, "active": False},
                  {"type": "hint", "t": t1 + HINT_LAG_MS + j[3], "name": name, "isEnd": True}]
        t, k, turn = t1, k + m, turn + 1
    # stable time order; at equal t: csrc off before on, then hints (replay order csrc→hint)
    hints.sort(key=lambda r: (r["t"], r["type"] != "csrc", r.get("active", False), r.get("csrc", 0), r.get("name", "")))
    return truth, hints, turn


def _wav_ms(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav)) as w:
        return round(w.getnframes() * 1000 / w.getframerate())


def mixed_audio(truth: list[dict], out: Path) -> str:
    """Store-only audio half: TTS per turn → STT; truth rescaled to the real WAV durations."""
    (out / "1-audio").mkdir(parents=True, exist_ok=True)
    turns: dict[int, list[dict]] = {}
    for r in truth:
        turns.setdefault(r["turn"], []).append(r)
    stt_rows, rescaled, shift = [], [], 0
    for i, rows in sorted(turns.items()):
        wav = tts(" ".join(_say(r["n"]) for r in rows), VOICES.get(rows[0]["label"], "aura-asteria-en"))
        (out / "1-audio" / f"turn{i:03d}.wav").write_bytes(wav)
        dur, t0 = _wav_ms(wav), rows[0]["t0_ms"] + shift
        shift += dur - (rows[-1]["t1_ms"] - rows[0]["t0_ms"])
        v = stt(wav)
        stt_rows.append({"turn": i, "speaker": rows[0]["speaker"], "csrc": rows[0]["csrc"],
                         "expect": [r["n"] for r in rows], "text": (v.get("text") or "").strip(),
                         "duration_ms": dur, "t0_ms": t0})
        step = dur / len(rows)
        rescaled += [{**r, "t0_ms": t0 + round(x * step), "t1_ms": t0 + round((x + 1) * step)}
                     for x, r in enumerate(rows)]
        print(f"  turn {i:03d} [{rows[0]['label']}] {rows[0]['n']}..{rows[-1]['n']} {dur} ms  STT: {stt_rows[-1]['text'][:40]!r}")
    (out / "2-stt.jsonl").write_bytes(_jsonl(stt_rows))
    (out / "truth-audio.jsonl").write_bytes(_jsonl(rescaled))
    return "deepgram-aura"


def mixed_main(a) -> int:
    if a.platform != "teams":
        raise SystemExit(f"--lane mixed: platform {a.platform!r} has no hint shape yet (teams: dom-outline)")
    speakers = a.speakers.split(",")
    if a.scenario == "solo":
        speakers = speakers[:1]
    cadence = a.cadence or 25
    gap_ms = a.gap_ms if a.gap_ms is not None else MIXED_GAP_MS[a.scenario]
    truth, hints, turns = mixed_script(a.n, a.scenario, speakers, cadence, gap_ms, a.seed, a.jitter_ms)
    assert [r["n"] for r in truth] == list(range(1, a.n + 1)), "truth must be exactly 1..N"
    out = Path(a.out) if a.out else Path(a.store) / a.platform / f"count-{a.scenario}-1to{a.n}"
    out.mkdir(parents=True, exist_ok=True)
    tb, hb = _jsonl(truth), _jsonl(hints)
    (out / "truth.jsonl").write_bytes(tb)
    (out / "hints.jsonl").write_bytes(hb)
    tts_engine = "none (scripted half only)"
    if a.audio:
        audio_out = Path(a.store) / a.platform / f"count-{a.scenario}-1to{a.n}"
        tts_engine = mixed_audio(truth, audio_out)
    manifest = {
        "lane": "mixed", "platform": a.platform, "scenario": a.scenario, "n": a.n, "turns": turns,
        "speakers": {s: {"name": MIXED_NAMES.get(s, s), "csrc": MIXED_CSRC.get(s)} for s in speakers},
        "args": {"cadence": cadence, "gap_ms": gap_ms, "seed": a.seed, "jitter_ms": a.jitter_ms},
        "timing": {"num_ms": NUM_MS, "start_ms": START_MS, "csrc_lead_ms": CSRC_LEAD_MS,
                   "hint_lag_ms": HINT_LAG_MS, "hint_shape": "dom-outline"},
        "voices": {s: VOICES.get(s, "aura-asteria-en") for s in speakers}, "tts": tts_engine,
        "sha256": {"truth.jsonl": hashlib.sha256(tb).hexdigest(), "hints.jsonl": hashlib.sha256(hb).hexdigest()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"· mixed/{a.platform} {a.scenario}: {len(truth)} numbers, {turns} turns, {len(hints)} hint rows → {out}")
    for f, h in manifest["sha256"].items():
        print(f"  sha256 {f} {h}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="meet", choices=["meet", "mixed"])
    ap.add_argument("--platform", default="teams")
    ap.add_argument("--scenario", default="silence",
                    choices=["silence", "overlap", "dynamic", "continuation", "solo"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--cadence", "--turn", dest="cadence", type=int, default=None)
    ap.add_argument("--speakers", default="A,B")
    ap.add_argument("--store", default=os.path.expanduser("~/vexa-test-rig/fixtures"))
    ap.add_argument("--out", default=None, help="mixed lane: where the scripted half goes")
    ap.add_argument("--gap-ms", type=int, default=None)
    ap.add_argument("--seed", type=int, default=499)
    ap.add_argument("--jitter-ms", type=int, default=0)
    ap.add_argument("--audio", action="store_true", help="mixed lane: also build the store-only audio half")
    a = ap.parse_args()
    if a.lane == "mixed":
        return mixed_main(a)
    a.cadence = a.cadence or 5

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
