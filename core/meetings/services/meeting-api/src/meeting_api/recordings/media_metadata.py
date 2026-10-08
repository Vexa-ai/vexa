"""Remux a completed WebM into a seekable container, without re-encoding its media."""
import json
import math
import subprocess
import tempfile
from pathlib import Path


def seekable_webm(data: bytes) -> tuple[bytes, float]:
    with tempfile.TemporaryDirectory(prefix="vexa-media-") as directory:
        source, target = Path(directory) / "source.webm", Path(directory) / "master.webm"
        source.write_bytes(data)
        # Restrict protocols: a recording is local media, never a network playlist.
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                            "-i", str(source), "-map", "0", "-c", "copy", "-y", str(target)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "json", str(target)], check=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid media duration")
            return target.read_bytes(), duration
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
            # Do not expose subprocess output or recording content to callers/logs.
            raise RuntimeError("Recording metadata finalization failed") from None
