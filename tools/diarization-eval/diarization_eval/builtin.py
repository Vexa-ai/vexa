"""Model-free candidate implementations; only ref and permute read the reference."""

import os
from pathlib import Path
import sys
import wave


def main():
    candidate, wav_name, output = sys.argv[1:]
    name = Path(wav_name).stem
    if candidate == "single":
        with wave.open(wav_name, "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        text = (f"SPEAKER {name} 1 0.000000 {duration:.6f} <NA> <NA> single <NA> <NA>\n"
                if duration else "")
    else:
        text = Path(os.environ["DIAR_REF_RTTM"]).read_text()
        if candidate == "permute":
            labels = {}
            rows = []
            for line in text.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                fields = line.split()
                fields[7] = labels.setdefault(fields[7], f"renamed_{len(labels)}")
                rows.append(" ".join(fields))
            text = "\n".join(rows) + ("\n" if rows else "")
        elif candidate != "ref":
            raise ValueError(f"unknown built-in: {candidate}")
    Path(output).write_text(text)


if __name__ == "__main__":
    main()
