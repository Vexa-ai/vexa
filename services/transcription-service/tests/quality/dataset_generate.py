from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

try:
    from scipy import signal
except ImportError:
    raise SystemExit("scipy not installed. Install with: pip install scipy")

from .phrases import LANGUAGES, DOMAINS, PHRASES, PHRASES_BY_DOMAIN


DEFAULT_SR = 16000
DEFAULT_SNR_LIST = [20.0, 10.0, 5.0]

# Natural noise types (simulated with filtered noise)
NOISE_TYPES = {
    "restaurant": {"color": "pink", "freq_range": (200, 2000)},  # Pink noise, mid frequencies
    "street": {"color": "brown", "freq_range": (100, 3000)},      # Brown noise, wider range
    "office": {"color": "pink", "freq_range": (300, 1500)},      # Pink noise, narrower
    "cafe": {"color": "pink", "freq_range": (150, 2500)},        # Pink noise, mid-high
    "white": {"color": "white", "freq_range": None},             # White noise (baseline)
}


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found. Install with: sudo apt-get install -y ffmpeg")
    return ffmpeg


def _tts_to_mp3(text: str, lang: str, out_mp3: Path) -> None:
    try:
        from gtts import gTTS
    except Exception as e:
        raise SystemExit("Missing gTTS. Install with: pip install gtts") from e
    tts = gTTS(text=text, lang=lang)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    tts.save(str(out_mp3))


def _mp3_to_wav(mp3_path: Path, wav_path: Path, sr: int) -> None:
    ffmpeg = _require_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(mp3_path),
            "-ar",
            str(sr),
            "-ac",
            "1",
            str(wav_path),
            "-y",
        ],
        check=True,
    )


def _pad_silence(wav_in: Path, wav_out: Path, sr: int, lead_s: float, trail_s: float) -> None:
    audio, file_sr = sf.read(str(wav_in), dtype="float32")
    if file_sr != sr:
        raise SystemExit(f"Unexpected sample rate in {wav_in}: {file_sr} != {sr}")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    lead = np.zeros(int(sr * lead_s), dtype=np.float32)
    trail = np.zeros(int(sr * trail_s), dtype=np.float32)
    out = np.concatenate([lead, audio.astype(np.float32), trail], axis=0)
    sf.write(str(wav_out), out, sr)


def _generate_colored_noise(
    length: int,
    sr: int,
    color: str,
    freq_range: Optional[tuple[int, int]],
    rng: np.random.Generator,
) -> np.ndarray:
    """Generate colored noise (white, pink, brown) with optional frequency filtering."""
    # Generate white noise base
    noise = rng.normal(0.0, 1.0, size=length).astype(np.float32)
    
    if color == "white":
        # No filtering needed
        pass
    elif color == "pink":
        # Pink noise: -3 dB/octave (1/f power spectrum)
        # Simple approximation using IIR filter
        # Pink noise filter: b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        #                    a = [1, -2.494956002, 2.017265875, -0.522189400]
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786], dtype=np.float32)
        a = np.array([1, -2.494956002, 2.017265875, -0.522189400], dtype=np.float32)
        noise = signal.lfilter(b, a, noise).astype(np.float32)
    elif color == "brown":
        # Brown noise: -6 dB/octave (1/f^2 power spectrum)
        # Simple approximation: integrate white noise
        b = np.array([1], dtype=np.float32)
        a = np.array([1, -0.99], dtype=np.float32)  # Simple integrator
        noise = signal.lfilter(b, a, noise).astype(np.float32)
    else:
        raise ValueError(f"Unknown noise color: {color}")
    
    # Apply frequency filtering if specified
    if freq_range is not None:
        low, high = freq_range
        nyquist = sr / 2.0
        low_norm = max(0.01, low / nyquist)
        high_norm = min(0.99, high / nyquist)
        sos = signal.butter(4, [low_norm, high_norm], btype="band", output="sos")
        noise = signal.sosfilt(sos, noise).astype(np.float32)
    
    # Normalize to prevent clipping
    noise = noise / (np.max(np.abs(noise)) + 1e-12)
    return noise


def _add_noise_snr(
    wav_in: Path,
    wav_out: Path,
    sr: int,
    snr_db: float,
    noise_type: str,
    rng: np.random.Generator,
) -> None:
    """Add natural noise to audio at specified SNR."""
    audio, file_sr = sf.read(str(wav_in), dtype="float32")
    if file_sr != sr:
        raise SystemExit(f"Unexpected sample rate in {wav_in}: {file_sr} != {sr}")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    audio = audio.astype(np.float32)

    # Get noise type config
    noise_config = NOISE_TYPES.get(noise_type, NOISE_TYPES["white"])
    color = noise_config["color"]
    freq_range = noise_config.get("freq_range")

    # Generate colored noise
    noise = _generate_colored_noise(len(audio), sr, color, freq_range, rng)

    # Calculate power and scale to target SNR
    sig_power = float(np.mean(audio * audio)) + 1e-12
    noise_power_target = sig_power / (10.0 ** (snr_db / 10.0))
    
    # Normalize noise to target power
    noise_power_actual = float(np.mean(noise * noise)) + 1e-12
    noise_scale = np.sqrt(noise_power_target / noise_power_actual)
    noise = (noise * noise_scale).astype(np.float32)

    # Mix
    noisy = audio + noise
    noisy = np.clip(noisy, -1.0, 1.0)
    sf.write(str(wav_out), noisy, sr)


def _write_silence(wav_out: Path, sr: int, seconds: float) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    audio = np.zeros(int(sr * seconds), dtype=np.float32)
    sf.write(str(wav_out), audio, sr)


def _write_near_silence(wav_out: Path, sr: int, seconds: float, rng: np.random.Generator) -> None:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    # Very low-level noise (approx -40 dBFS amplitude)
    noise = rng.normal(0.0, 0.01, size=int(sr * seconds)).astype(np.float32)
    noise = np.clip(noise, -1.0, 1.0)
    sf.write(str(wav_out), noise, sr)


def _write_silence_with_type(
    wav_out: Path,
    sr: int,
    seconds: float,
    silence_type: str,
    rng: np.random.Generator,
) -> None:
    """Generate silence with different characteristics."""
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    length = int(sr * seconds)
    
    if silence_type == "pure":
        # Digital silence (zeros)
        audio = np.zeros(length, dtype=np.float32)
    elif silence_type == "hiss":
        # Very low-level white noise (mic hiss, ~-40 dBFS)
        audio = rng.normal(0.0, 0.01, size=length).astype(np.float32)
    elif silence_type == "pink_floor":
        # Pink noise floor (room tone)
        white = rng.normal(0.0, 0.005, size=length).astype(np.float32)
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786], dtype=np.float32)
        a = np.array([1, -2.494956002, 2.017265875, -0.522189400], dtype=np.float32)
        audio = signal.lfilter(b, a, white).astype(np.float32)
    elif silence_type == "brown_floor":
        # Brown noise floor (distant rumble)
        white = rng.normal(0.0, 0.003, size=length).astype(np.float32)
        b = np.array([1], dtype=np.float32)
        a = np.array([1, -0.99], dtype=np.float32)
        audio = signal.lfilter(b, a, white).astype(np.float32)
    elif silence_type == "hum":
        # 50/60 Hz hum (simulated with low-frequency sine)
        t = np.arange(length) / sr
        freq = 50.0 if rng.random() < 0.5 else 60.0
        audio = (0.002 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    elif silence_type == "hvac":
        # HVAC-like noise (low-frequency filtered pink)
        white = rng.normal(0.0, 0.008, size=length).astype(np.float32)
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786], dtype=np.float32)
        a = np.array([1, -2.494956002, 2.017265875, -0.522189400], dtype=np.float32)
        pink = signal.lfilter(b, a, white).astype(np.float32)
        # Low-pass filter for HVAC
        nyquist = sr / 2.0
        sos = signal.butter(4, 200 / nyquist, btype="low", output="sos")
        audio = signal.sosfilt(sos, pink).astype(np.float32)
    else:
        raise ValueError(f"Unknown silence type: {silence_type}")
    
    audio = np.clip(audio, -1.0, 1.0)
    sf.write(str(wav_out), audio, sr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-dir", default="tests/quality_dataset", help="Output dataset directory")
    ap.add_argument("--languages", nargs="*", default=LANGUAGES)
    ap.add_argument("--domains", nargs="*", default=["general"], help=f"Domains to generate. Options: {', '.join(DOMAINS)}")
    ap.add_argument("--phrases-per-domain", type=int, default=4, help="Max phrases per language per domain (keeps dataset size manageable)")
    ap.add_argument("--sample-rate", type=int, default=DEFAULT_SR)
    ap.add_argument("--snr-db", nargs="*", type=float, default=DEFAULT_SNR_LIST)
    ap.add_argument("--lead-silence", type=float, default=None, help="Fixed leading silence duration (seconds). If not set, uses random 0-max-silence")
    ap.add_argument("--trail-silence", type=float, default=None, help="Fixed trailing silence duration (seconds). If not set, uses random 0-max-silence")
    ap.add_argument("--max-silence", type=float, default=20.0, help="Maximum random silence duration (seconds, default 20.0)")
    ap.add_argument("--noise-types", nargs="*", default=["restaurant", "street", "office"], help="Noise types to use")
    ap.add_argument("--silence-count", type=int, default=10, help="Number of diverse silence examples to generate")
    ap.add_argument("--silence-min-duration", type=float, default=1.0, help="Minimum silence duration (seconds)")
    ap.add_argument("--silence-max-duration", type=float, default=20.0, help="Maximum silence duration (seconds)")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    dataset_dir = Path(args.dataset_dir)
    audio_dir = dataset_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset_dir / "manifest.jsonl"

    rng = np.random.default_rng(args.seed)

    entries: list[dict] = []

    # Generate diverse silence examples (pure silence with different characteristics)
    silence_types = ["pure", "hiss", "pink_floor", "brown_floor", "hum", "hvac"]
    for i in range(args.silence_count):
        # Random duration
        duration = rng.uniform(args.silence_min_duration, args.silence_max_duration)
        # Random silence type
        silence_type = rng.choice(silence_types)
        
        case_id = f"silence_{silence_type}_{i:02d}"
        silence_wav = audio_dir / f"{case_id}.wav"
        _write_silence_with_type(silence_wav, args.sample_rate, duration, silence_type, rng)
        
        entries.append(
            {
                "case_id": case_id,
                "language": None,
                "domain": "silence",
                "kind": "silence",
                "snr_db": None,
                "noise_type": None,
                "silence_type": silence_type,
                "duration_sec": round(duration, 2),
                "lead_silence_sec": None,
                "trail_silence_sec": None,
                "audio_path": str(silence_wav.relative_to(dataset_dir)),
                "expected_text": "",
            }
        )
    
    # Generate silence domain: silence with background noise (no speech, just ambient noise)
    if "silence" in args.domains:
        for noise_type in args.noise_types:
            if noise_type not in NOISE_TYPES:
                continue
            for i in range(args.silence_count // 2):  # Half as many noise+silence cases
                duration = rng.uniform(args.silence_min_duration, args.silence_max_duration)
                
                # Create pure silence
                temp_silence = audio_dir / f"temp_silence_{i}.wav"
                _write_silence(temp_silence, args.sample_rate, duration)
                
                # Add background noise at various levels (simulating ambient noise without speech)
                for snr_db in [30.0, 20.0, 15.0, 10.0]:  # Higher SNR = quieter background
                    case_id = f"silence_domain_{noise_type}_{snr_db:.0f}db_{i:02d}"
                    silence_noise_wav = audio_dir / f"{case_id}.wav"
                    _add_noise_snr(temp_silence, silence_noise_wav, args.sample_rate, snr_db, noise_type, rng)
                    
                    entries.append(
                        {
                            "case_id": case_id,
                            "language": None,
                            "domain": "silence",
                            "kind": "silence",
                            "snr_db": float(snr_db),
                            "noise_type": noise_type,
                            "silence_type": f"ambient_{noise_type}",
                            "duration_sec": round(duration, 2),
                            "lead_silence_sec": None,
                            "trail_silence_sec": None,
                            "audio_path": str(silence_noise_wav.relative_to(dataset_dir)),
                            "expected_text": "",
                        }
                    )
                
                # Cleanup temp file
                try:
                    temp_silence.unlink(missing_ok=True)
                except:
                    pass

    for lang in args.languages:
        if lang not in PHRASES_BY_DOMAIN:
            raise SystemExit(f"Unknown language: {lang}. Supported: {sorted(PHRASES_BY_DOMAIN.keys())}")

        # Validate domains (skip silence domain - it's handled separately)
        for d in args.domains:
            if d == "silence":
                continue  # Silence domain is handled separately, no phrases needed
            if d not in PHRASES_BY_DOMAIN[lang]:
                raise SystemExit(f"Unknown domain '{d}' for language '{lang}'. Available: {sorted(PHRASES_BY_DOMAIN[lang].keys())}")

        for domain in args.domains:
            # Skip silence domain - it's handled separately with noise-only cases
            if domain == "silence":
                continue
            
            domain_phrases = list(PHRASES_BY_DOMAIN[lang][domain])
            if args.phrases_per_domain > 0:
                domain_phrases = domain_phrases[: args.phrases_per_domain]

            for i, text in enumerate(domain_phrases):
                # Include domain in id to keep uniqueness across domains
                case_id = f"{lang}_{domain}_{i:02d}"
                mp3_path = audio_dir / f"{case_id}.mp3"
                wav_raw = audio_dir / f"{case_id}.raw.wav"
                wav_clean = audio_dir / f"{case_id}.wav"

                # 1) TTS to mp3
                _tts_to_mp3(text, lang, mp3_path)

                # 2) mp3 -> wav 16k mono
                _mp3_to_wav(mp3_path, wav_raw, args.sample_rate)

                # 3) add leading/trailing silence (all clips have silence+speech+silence)
                # Use random silence lengths if not fixed
                lead_s = args.lead_silence if args.lead_silence is not None else rng.uniform(0.0, args.max_silence)
                trail_s = args.trail_silence if args.trail_silence is not None else rng.uniform(0.0, args.max_silence)
                _pad_silence(wav_raw, wav_clean, args.sample_rate, lead_s, trail_s)

                # clean entry (still has silence padding, but no noise)
                entries.append(
                    {
                        "case_id": case_id,
                        "language": lang,
                        "domain": domain,
                        "kind": "clean",
                        "snr_db": None,
                        "noise_type": None,
                        "lead_silence_sec": round(lead_s, 2),
                        "trail_silence_sec": round(trail_s, 2),
                        "audio_path": str(wav_clean.relative_to(dataset_dir)),
                        "expected_text": text,
                    }
                )

                # noisy variants with different noise types
                for noise_type in args.noise_types:
                    if noise_type not in NOISE_TYPES:
                        print(f"Warning: Unknown noise type '{noise_type}', skipping. Available: {list(NOISE_TYPES.keys())}")
                        continue
                    for snr_db in args.snr_db:
                        noisy_path = audio_dir / f"{case_id}.{noise_type}.snr{int(snr_db)}.wav"
                        _add_noise_snr(wav_clean, noisy_path, args.sample_rate, float(snr_db), noise_type, rng)
                        entries.append(
                            {
                                "case_id": case_id,
                                "language": lang,
                                "domain": domain,
                                "kind": "noisy",
                                "snr_db": float(snr_db),
                                "noise_type": noise_type,
                                "lead_silence_sec": round(lead_s, 2),
                                "trail_silence_sec": round(trail_s, 2),
                                "audio_path": str(noisy_path.relative_to(dataset_dir)),
                                "expected_text": text,
                            }
                        )

                # cleanup temp artifacts but keep raw wav if you want debugging
                try:
                    mp3_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                try:
                    wav_raw.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass

    with manifest_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"Wrote dataset: {dataset_dir}")
    print(f"- manifest: {manifest_path}")
    print(f"- audio:    {audio_dir}")
    print(f"- cases:    {len(entries)}")


if __name__ == "__main__":
    main()


