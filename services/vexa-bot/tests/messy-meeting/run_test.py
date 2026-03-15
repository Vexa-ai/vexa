#!/usr/bin/env python3
"""
Messy meeting transcription pipeline test.

Sends pre-generated per-speaker WAV files directly to transcription-service
via HTTP POST, then validates output against expected keywords.

No bot, no browser, no Redis — pure audio→text pipeline validation.

Usage:
    python run_test.py --scenario full-messy
    python run_test.py --all
    python run_test.py --scenario overlap --no-generate

Prerequisites:
    - transcription-service on localhost:8083 (or TRANSCRIPTION_SERVICE_URL)
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import requests

from scenarios import SCENARIOS

CACHE_DIR = Path(__file__).parent / "cache"
TRANSCRIPTION_URL = os.environ.get(
    "TRANSCRIPTION_SERVICE_URL", "http://localhost:8083/v1/audio/transcriptions"
)
TRANSCRIPTION_TOKEN = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "your_secure_token_here")

# Known hallucination phrases — loaded from WhisperLive files if available
HALLUCINATION_DIR = Path(__file__).parent / "../../../WhisperLive/hallucinations"


def load_hallucination_phrases() -> set[str]:
    phrases = set()
    for lang_file in HALLUCINATION_DIR.glob("*.txt"):
        for line in lang_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                phrases.add(line.strip().lower())
    return phrases


HALLUCINATIONS = load_hallucination_phrases()


# ─── Transcription ────────────────────────────────────────────────────────────

def transcribe_wav(wav_path: Path, speaker_name: str) -> dict:
    """POST a WAV file to transcription-service, return result dict."""
    with open(wav_path, "rb") as f:
        resp = requests.post(
            TRANSCRIPTION_URL,
            files={"file": (f"{speaker_name}.wav", f, "audio/wav")},
            data={"model": "large-v3-turbo"},
            headers={"Authorization": f"Bearer {TRANSCRIPTION_TOKEN}"},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()


# ─── Validators ───────────────────────────────────────────────────────────────

class CheckResult:
    def __init__(self, name: str, passed: bool, detail: str = "", advisory: bool = False):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.advisory = advisory

    def __str__(self):
        icon = "✅" if self.passed else ("⚠️" if self.advisory else "❌")
        s = f"  {icon} {self.name}"
        if self.detail:
            s += f" ({self.detail})"
        return s


def check_keyword_attribution(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Verify each speaker's keywords appear in their transcription."""
    speakers = manifest["speakers"]
    missing = []

    for speaker_name, info in speakers.items():
        keywords = [kw.lower() for kw in info.get("keywords", [])]
        if not keywords:
            continue

        text = results.get(speaker_name, {}).get("text", "").lower()

        for kw in keywords:
            if kw not in text:
                missing.append(f"{speaker_name}:{kw}")

    if missing:
        return CheckResult("keyword_attribution", False, f"missing: {', '.join(missing[:8])}")
    return CheckResult("keyword_attribution", True)


def check_no_cross_contamination(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Verify speaker A's unique keywords don't appear in speaker B's transcription.

    Only checks keywords that are unique to one speaker — shared words
    (like common English) are excluded.
    """
    speakers = manifest["speakers"]
    # Build per-speaker keyword sets
    speaker_kws: dict[str, set[str]] = {}
    for name, info in speakers.items():
        speaker_kws[name] = {kw.lower() for kw in info.get("keywords", [])}

    # Find keywords unique to each speaker
    all_kws = set()
    for kws in speaker_kws.values():
        all_kws |= kws

    violations = []
    for speaker_name, kws in speaker_kws.items():
        if not kws:
            continue
        # Keywords unique to this speaker
        unique_kws = kws - set().union(*(v for k, v in speaker_kws.items() if k != speaker_name))

        for other_name in speakers:
            if other_name == speaker_name:
                continue
            other_text = results.get(other_name, {}).get("text", "").lower()
            for kw in unique_kws:
                if kw in other_text:
                    violations.append(f"{kw} in {other_name}")

    if violations:
        return CheckResult("no_cross_contamination", False, f"leaked: {', '.join(violations[:5])}")
    return CheckResult("no_cross_contamination", True)


def check_segment_duration(results: dict[str, dict], manifest: dict) -> CheckResult:
    """In direct POST mode, check that transcription returned reasonable duration.

    The 30s mega-segment check applies to the bot's streaming pipeline, not batch POST.
    Here we just verify durations are plausible (not zero, not wildly wrong).
    """
    durations = []
    for speaker, result in results.items():
        dur = result.get("duration", 0)
        if isinstance(dur, str):
            dur = float(dur)
        durations.append((speaker, dur))

    zero_dur = [s for s, d in durations if d == 0]
    if zero_dur:
        return CheckResult("segment_duration", False, f"zero duration: {', '.join(zero_dur)}")

    max_dur = max(d for _, d in durations) if durations else 0
    return CheckResult("segment_duration", True, f"max {max_dur:.1f}s")


def check_no_hallucinations(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Check for known hallucination phrases and repetition loops."""
    found = []
    for speaker, result in results.items():
        text = result.get("text", "").strip().lower()
        if not text:
            continue

        # Check against known phrases
        if text in HALLUCINATIONS:
            found.append(f"{speaker}: \"{text[:40]}\"")
            continue

        # Check for repetition loops (same 5+ word phrase repeated 3+ times)
        words = text.split()
        if len(words) >= 15:
            for ngram_len in range(5, min(10, len(words) // 3 + 1)):
                for i in range(len(words) - ngram_len * 3 + 1):
                    ngram = " ".join(words[i:i + ngram_len])
                    count = text.count(ngram)
                    if count >= 3:
                        found.append(f"{speaker}: repetition \"{ngram[:30]}\" x{count}")
                        break
                else:
                    continue
                break

    if found:
        return CheckResult("no_hallucinations", False, "; ".join(found[:3]))
    return CheckResult("no_hallucinations", True)


def check_no_duplicates(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Check for duplicate transcription text across speakers (shouldn't happen with separate POSTs)."""
    texts = []
    for speaker, result in results.items():
        t = result.get("text", "").strip()
        if t:
            texts.append(t)

    unique = set(texts)
    dupes = len(texts) - len(unique)
    return CheckResult("no_duplicates", dupes == 0, f"{dupes} duplicates" if dupes else "")


def check_multilingual(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Verify non-English speakers detected with correct language."""
    speakers = manifest["speakers"]
    findings = []

    for speaker_name, info in speakers.items():
        expected_lang = info.get("language", "en")
        if expected_lang == "en":
            continue

        result = results.get(speaker_name, {})
        detected = result.get("language", "unknown")

        if expected_lang in str(detected):
            findings.append(f"{speaker_name}: {detected}")
        else:
            return CheckResult(
                "multilingual", False,
                f"{speaker_name} expected {expected_lang}, got {detected}"
            )

    if findings:
        return CheckResult("multilingual", True, ", ".join(findings))
    return CheckResult("multilingual", True, "no multilingual speakers")


def check_draft_confirmed(results: dict[str, dict], manifest: dict) -> CheckResult:
    """Not applicable for direct HTTP POST — always passes."""
    return CheckResult("draft_confirmed", True, "n/a for direct POST mode")


# Map check names to functions
CHECK_REGISTRY = {
    "keyword_attribution": check_keyword_attribution,
    "no_cross_contamination": check_no_cross_contamination,
    "segment_duration": check_segment_duration,
    "draft_confirmed": check_draft_confirmed,
    "no_hallucinations": check_no_hallucinations,
    "no_duplicates": check_no_duplicates,
    "multilingual": check_multilingual,
}


# ─── Main runner ──────────────────────────────────────────────────────────────

async def run_scenario(name: str, scenario: dict, skip_generate: bool = False) -> list[CheckResult]:
    """Run a single scenario: generate audio → transcribe → validate."""
    print(f"\n[{name}] {scenario['description']}")

    manifest_path = CACHE_DIR / name / "manifest.json"

    # Step 1: Generate audio if needed
    if not skip_generate:
        from generate_audio import generate_scenario
        await generate_scenario(name, scenario)

    if not manifest_path.exists():
        print(f"  ERROR: manifest not found at {manifest_path}")
        print(f"  Run: python generate_audio.py --scenario {name}")
        return [CheckResult("setup", False, "manifest missing")]

    manifest = json.loads(manifest_path.read_text())

    # Step 2: Transcribe each speaker's WAV
    transcription_results: dict[str, dict] = {}

    for speaker_name, info in manifest["speakers"].items():
        wav_path = CACHE_DIR / name / info["wav"]
        if not wav_path.exists():
            print(f"  ERROR: WAV not found: {wav_path}")
            return [CheckResult("setup", False, f"{info['wav']} missing")]

        print(f"  Transcribing {speaker_name} ({info['wav']}, {info['duration_s']}s)...")
        try:
            result = transcribe_wav(wav_path, speaker_name)
            text = result.get("text", "")
            lang = result.get("language", "unknown")
            dur = result.get("duration", 0)
            print(f"    → lang={lang}, dur={dur:.1f}s, text=\"{text[:80]}{'...' if len(text)>80 else ''}\"")
            transcription_results[speaker_name] = result
        except Exception as e:
            print(f"    → FAILED: {e}")
            return [CheckResult("transcription", False, f"{speaker_name}: {e}")]

    # Print input vs output for human review
    print_report(scenario, transcription_results)

    # Step 3: Run checks
    results = []
    for check_name in manifest.get("checks", scenario.get("checks", [])):
        check_fn = CHECK_REGISTRY.get(check_name)
        if not check_fn:
            results.append(CheckResult(check_name, False, "unknown check"))
            continue

        result = check_fn(transcription_results, manifest)

        # Mark interjection-related failures as advisory
        if check_name == "keyword_attribution" and not result.passed:
            advisory_speakers = {
                s for s, info in manifest["speakers"].items()
                if info.get("advisory_utterances")
            }
            if all(
                m.split(":")[0] in advisory_speakers
                for m in result.detail.replace("missing: ", "").split(", ")
                if ":" in m
            ):
                result.advisory = True

        results.append(result)

    return results


def print_report(scenario: dict, transcription_results: dict[str, dict]):
    """Print input vs output side by side for human validation."""
    print("\n" + "=" * 80)
    print("HUMAN REVIEW: INPUT vs OUTPUT")
    print("=" * 80)

    for utt in scenario["utterances"]:
        speaker = utt["speaker"]
        input_text = utt["text"]
        start = utt["start_s"]
        is_advisory = utt.get("advisory", False)
        has_noise = utt.get("noise_db") is not None

        tag = ""
        if is_advisory:
            tag = " [interjection]"
        if has_noise:
            tag += f" [noise {utt['noise_db']}dB]"

        print(f"\n  {speaker} @ {start}s{tag}")
        print(f"  INPUT:  {input_text}")

    print("\n" + "-" * 80)
    print("TRANSCRIPTION OUTPUT (full text per speaker)")
    print("-" * 80)

    for speaker, result in transcription_results.items():
        text = result.get("text", "")
        lang = result.get("language", "?")
        dur = result.get("duration", 0)
        print(f"\n  {speaker} (lang={lang}, dur={dur:.1f}s):")

        # Wrap text at 100 chars for readability
        words = text.split()
        lines = []
        line = ""
        for w in words:
            if len(line) + len(w) + 1 > 100:
                lines.append(line)
                line = w
            else:
                line = f"{line} {w}" if line else w
        if line:
            lines.append(line)
        for l in lines:
            print(f"    {l}")

    # Save full report to file for later review
    report_path = CACHE_DIR / "last_report.txt"
    with open(report_path, "w") as f:
        f.write("MESSY MEETING TRANSCRIPTION REPORT\n")
        f.write(f"Generated: {__import__('datetime').datetime.now().isoformat()}\n\n")
        for utt in scenario["utterances"]:
            tag = ""
            if utt.get("advisory"):
                tag = " [interjection]"
            if utt.get("noise_db") is not None:
                tag += f" [noise {utt['noise_db']}dB]"
            f.write(f"{utt['speaker']} @ {utt['start_s']}s{tag}\n")
            f.write(f"  INPUT: {utt['text']}\n\n")
        f.write("-" * 80 + "\n\n")
        for speaker, result in transcription_results.items():
            f.write(f"{speaker} (lang={result.get('language','?')}, dur={result.get('duration',0):.1f}s):\n")
            f.write(f"  {result.get('text','')}\n\n")
    print(f"\n  Report saved to: {report_path}")


async def main():
    parser = argparse.ArgumentParser(description="Messy meeting transcription test")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", type=str, help="Scenario name")
    group.add_argument("--all", action="store_true", help="Run all scenarios")
    parser.add_argument("--no-generate", action="store_true", help="Skip audio generation")
    args = parser.parse_args()

    if args.all:
        names = sorted(SCENARIOS.keys())
    else:
        if args.scenario not in SCENARIOS:
            available = ", ".join(sorted(SCENARIOS.keys()))
            print(f"Unknown scenario '{args.scenario}'. Available: {available}")
            sys.exit(1)
        names = [args.scenario]

    # Check transcription service
    print(f"Transcription service: {TRANSCRIPTION_URL}")
    try:
        health_url = TRANSCRIPTION_URL.rsplit("/", 2)[0] + "/health"
        r = requests.get(health_url, timeout=5)
        print(f"Health check: {r.status_code}\n")
    except Exception as e:
        print(f"Health check failed: {e}")
        print("Is transcription-service running?\n")

    print(f"Running {len(names)} scenario(s)...")

    all_results = {}
    for name in names:
        results = await run_scenario(name, SCENARIOS[name], skip_generate=args.no_generate)
        all_results[name] = results

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total_pass = 0
    total_fail = 0
    total_advisory = 0

    for name, results in all_results.items():
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        advisories = sum(1 for r in results if r.advisory and not r.passed)
        fails = total - passed - advisories

        total_pass += passed
        total_fail += fails
        total_advisory += advisories

        status = "PASS" if fails == 0 else "FAIL"
        print(f"\n[{name}] {passed}/{total} checks passed  [{status}]")
        for r in results:
            print(str(r))

    print(f"\nTotal: {total_pass} passed, {total_fail} failed, {total_advisory} advisory")
    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
