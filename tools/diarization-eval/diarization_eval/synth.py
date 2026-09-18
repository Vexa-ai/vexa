"""Generate two deterministic, model-free 20-second fixture pairs."""

import argparse
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(generate_fixtures(parser.parse_args().out))
