from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .db import start_run, insert_case, finalize_run
from .metrics import wer, cer, normalize_text
from .vad_silero import is_likely_silence


DEFAULT_THRESHOLDS = {
    "clean": 0.20,
    "noisy_20": 0.30,
    "noisy_10": 0.35,
    "noisy_5": 0.45,
}


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def _threshold_for(entry: dict[str, Any]) -> float:
    kind = entry.get("kind")
    if kind == "clean":
        return float(DEFAULT_THRESHOLDS["clean"])
    if kind == "noisy":
        snr = entry.get("snr_db", None)
        if snr is None:
            return float(DEFAULT_THRESHOLDS["noisy_10"])
        snr_int = int(round(float(snr)))
        if snr_int >= 20:
            return float(DEFAULT_THRESHOLDS["noisy_20"])
        if snr_int >= 10:
            return float(DEFAULT_THRESHOLDS["noisy_10"])
        return float(DEFAULT_THRESHOLDS["noisy_5"])
    return 0.0


def _post_transcribe(
    api_url: str,
    api_token: str,
    audio_path: Path,
    *,
    language: Optional[str],
    timeout_s: float,
) -> dict[str, Any]:
    headers = {}
    if api_token:
        headers["X-API-Key"] = api_token
    with audio_path.open("rb") as f:
        files = {"file": (audio_path.name, f, "audio/wav")}
        data = {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities": "segment",
        }
        if language:
            data["language"] = language
        r = requests.post(api_url, headers=headers, files=files, data=data, timeout=timeout_s)
        r.raise_for_status()
        return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default=os.getenv("TRANSCRIPTION_API_URL", "http://localhost:8083/v1/audio/transcriptions"))
    ap.add_argument("--api-token", default=os.getenv("API_TOKEN", ""))
    ap.add_argument("--dataset-dir", default="tests/quality_dataset")
    ap.add_argument("--languages", nargs="*", default=["en", "es", "fr", "de", "it", "pt", "ru"])
    ap.add_argument("--domains", nargs="*", default=[], help="Optional: filter dataset by domains (e.g. healthcare finance legal software)")
    ap.add_argument("--run-name", default=f"quality_{int(time.time())}")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--max-cases", type=int, default=0, help="If >0, limit number of cases per language/kind")
    ap.add_argument("--use-vad", action="store_true", help="Use Silero VAD to validate silence detection")
    args = ap.parse_args()

    dataset_dir = Path(args.dataset_dir)
    manifest_path = dataset_dir / "manifest.jsonl"
    if not manifest_path.exists():
        raise SystemExit(f"Missing manifest: {manifest_path}. Generate dataset first: python3 -m tests.quality.dataset_generate")

    db_path = dataset_dir / "test_results.db"

    meta = {
        "thresholds": DEFAULT_THRESHOLDS,
        "languages": args.languages,
        "domains": args.domains,
    }
    run_id = start_run(db_path, args.run_name, args.api_url, meta)

    entries = _load_manifest(manifest_path)

    # Optional limiting to keep CI fast
    per_key_seen: dict[tuple[str, str, str, str, str], int] = {}

    total = 0
    passed = 0

    for e in entries:
        lang = e.get("language")
        if lang is not None and lang not in args.languages:
            continue

        domain = e.get("domain")
        if args.domains:
            # include only matching domains; silence entries have domain=None and should be kept
            if domain is not None and domain not in args.domains:
                continue

        kind = str(e.get("kind"))
        case_id = str(e.get("case_id"))
        audio_rel = str(e.get("audio_path"))
        audio_path = dataset_dir / audio_rel
        expected_text = str(e.get("expected_text", ""))
        snr_db = e.get("snr_db", None)
        noise_type = e.get("noise_type", None)

        if args.max_cases and lang:
            noise_key = str(noise_type) if noise_type else "na"
            domain_key = str(domain) if domain else "na"
            key = (
                lang,
                domain_key,
                kind,
                str(int(round(float(snr_db)))) if snr_db is not None else "na",
                noise_key,
            )
            seen = per_key_seen.get(key, 0)
            if seen >= args.max_cases:
                continue
            per_key_seen[key] = seen + 1

        total += 1
        started = time.time()

        transcript = ""
        w = None
        c = None
        ok = False
        err = ""
        duration_sec = None

        try:
            resp = _post_transcribe(args.api_url, args.api_token, audio_path, language=lang, timeout_s=args.timeout)
            transcript = str(resp.get("text", "") or "")
            duration_sec = float(resp.get("duration", 0.0) or 0.0)

            if kind == "silence":
                # For silence: transcript should be empty
                transcript_empty = normalize_text(transcript) == ""
                
                # Optionally validate with Silero VAD
                if args.use_vad:
                    try:
                        vad_says_silence = is_likely_silence(audio_path, threshold=0.5, min_speech_ratio=0.1)
                        # If VAD says silence, transcript must be empty
                        ok = transcript_empty and vad_says_silence
                    except Exception as vad_err:
                        # If VAD fails, fall back to transcript-only check
                        ok = transcript_empty
                else:
                    ok = transcript_empty
            else:
                w = float(wer(expected_text, transcript))
                c = float(cer(expected_text, transcript))
                thr = _threshold_for(e)
                ok = w <= thr
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"
            ok = False

        took = time.time() - started
        passed += 1 if ok else 0

        insert_case(
            db_path,
            run_id,
            case_id=case_id,
            language=lang,
            kind=kind,
            snr_db=float(snr_db) if snr_db is not None else None,
            audio_path=audio_rel,
            expected_text=expected_text,
            transcript=transcript,
            wer=w,
            cer=c,
            passed=ok,
            error=err or None,
            duration_sec=duration_sec,
            meta={"latency_sec": took, "noise_type": noise_type, "domain": domain},
        )

        status = "PASS" if ok else "FAIL"
        extra = ""
        if kind == "silence":
            extra = f" transcript='{normalize_text(transcript)[:60]}'"
        else:
            noise_str = f" noise={noise_type}" if noise_type else ""
            domain_str = f" domain={domain}" if domain else ""
            extra = f" WER={w:.3f} (thr={_threshold_for(e):.2f}){noise_str}{domain_str}"
        print(f"[{status}] {case_id} {lang or '-'} {kind} snr={snr_db} took={took:.2f}s{extra}")

    finalize_run(db_path, run_id)

    print("")
    print(f"Run {run_id} done: {passed}/{total} passed")
    print(f"DB: {db_path}")

    if passed != total:
        sys.exit(2)


if __name__ == "__main__":
    main()


