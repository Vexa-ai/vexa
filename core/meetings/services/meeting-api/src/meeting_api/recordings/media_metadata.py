"""Remux a completed WebM into a seekable container, without re-encoding its media.

A failure is a typed fault, :class:`MediaMetadataFault` — ``source`` is the tool that failed,
``kind`` what went wrong — logged as ``recording_metadata_fault`` before it is raised. Neither the
log nor the exception carries subprocess output or recording content.
"""
import json
import math
import subprocess
import tempfile
from pathlib import Path

from ..obs import log_event


class MediaMetadataFault(RuntimeError):
    """The master could not be made seekable.

    ``source``: ``ffmpeg`` (the remux) or ``ffprobe`` (the duration probe). ``kind``:
    ``unavailable`` (the tool is not installed or cannot start) · ``timeout`` · ``failed`` (it ran
    and exited non-zero, or wrote no output) · ``invalid_duration`` (the probe answered, but with no
    positive, finite duration)."""

    def __init__(self, source: str, kind: str) -> None:
        super().__init__(f"Recording metadata finalization failed ({source}: {kind})")
        self.source = source
        self.kind = kind


def _fault(source: str, kind: str) -> MediaMetadataFault:
    log_event("recording_metadata_fault", audience="operator", level="warning",
              span="recordings.finalize", fields={"source": source, "kind": kind})
    return MediaMetadataFault(source, kind)


def _run(source: str, argv: list, *, timeout: int, stdout) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, check=True, stdout=stdout, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise _fault(source, "timeout") from None
    except subprocess.CalledProcessError:
        raise _fault(source, "failed") from None
    except OSError:
        raise _fault(source, "unavailable") from None


def seekable_webm(data: bytes) -> tuple[bytes, float]:
    with tempfile.TemporaryDirectory(prefix="vexa-media-") as directory:
        source, target = Path(directory) / "source.webm", Path(directory) / "master.webm"
        source.write_bytes(data)
        # Restrict protocols: a recording is local media, never a network playlist.
        _run("ffmpeg", ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                        "-i", str(source), "-map", "0", "-c", "copy", "-y", str(target)],
             timeout=120, stdout=subprocess.DEVNULL)
        probe = _run("ffprobe", ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(target)], timeout=30, stdout=subprocess.PIPE)
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (ValueError, KeyError, TypeError):
            raise _fault("ffprobe", "invalid_duration") from None
        if not math.isfinite(duration) or duration <= 0:
            raise _fault("ffprobe", "invalid_duration")
        try:
            return target.read_bytes(), duration
        except OSError:
            raise _fault("ffmpeg", "failed") from None
