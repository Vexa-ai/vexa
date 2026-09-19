"""Generate two deterministic, model-free 20-second fixture pairs."""

import argparse
import math

import numpy as np
from pathlib import Path
import wave


def generate_fixtures(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "two_speakers": [(0, 10, "A"), (10, 20, "B")],
        "three_speakers": [(0, 6, "A"), (6, 9, "B"), (9, 15, "A"), (15, 20, "C")],
    }
    for name, turns in fixtures.items():
        with wave.open(str(out / f"{name}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(20 * 16000 * 2))
        (out / f"{name}.rttm").write_text("".join(
            f"SPEAKER {name} 1 {start:.6f} {end-start:.6f} <NA> <NA> {label} <NA> <NA>\n"
            for start, end, label in turns
        ))
    return out


def generate_sine(out, *, level_dbfs, duration=5.0):
    """Write a 440 Hz PCM16 sine at the requested RMS dBFS, and one-speaker RTTM."""
    if not math.isfinite(level_dbfs) or level_dbfs > -3.011:
        raise ValueError("sine RMS level must be finite and <= -3.011 dBFS (no clipping)")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    samples = np.sin(2 * np.pi * 440 * np.arange(round(duration * 16000)) / 16000)
    samples *= math.sqrt(2) * 10 ** (level_dbfs / 20)
    with wave.open(str(out / "sine.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.rint(samples * 32768).astype("<i2").tobytes())
    (out / "sine.rttm").write_text(
        f"SPEAKER sine 1 0 {len(samples) / 16000:.6f} <NA> <NA> A <NA> <NA>\n")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--level-dbfs", type=float, help="generate a sine at this RMS dBFS instead of silent fixtures")
    parser.add_argument("--duration", type=float, default=5.0, help="sine duration in seconds (default 5)")
    args = parser.parse_args()
    if args.level_dbfs is None:
        print(generate_fixtures(args.out))
    else:
        print(generate_sine(args.out, level_dbfs=args.level_dbfs, duration=args.duration))
