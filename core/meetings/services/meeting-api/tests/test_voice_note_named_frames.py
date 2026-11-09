"""The voice-note door accepts NAMED capture.v1 frames (per-track capture with speaker names).

Layout (@vexa/capture-codec encodeAudioFrame): [Int32LE track|0x80000000][Float64LE ts][Int32LE nameLen]
[UTF-8 name, zero-padded to 4B][Float32LE pcm…]. Before, any frame with the name flag was refused
(bad_frame), so a per-speaker capture could not reach a note. Card brain:card/66d7eaa642e5.
"""
from __future__ import annotations

import struct
import time

from meeting_api.voice_notes.router import (MAX_FRAME_SAMPLES, MAX_NAME_BYTES, MAX_TRACKS, MIN_NAMED_SAMPLES,
                                            frame_parse, frame_samples)

TS = time.time() * 1000


def named(track: int, name: str, samples: int, ts: float = TS) -> bytes:
    nb = name.encode("utf-8")
    pad = (len(nb) + 3) & ~3
    head = struct.pack("<Idi", (track | 0x80000000) & 0xFFFFFFFF, ts, len(nb))
    return head + nb + b"\0" * (pad - len(nb)) + struct.pack(f"<{samples}f", *([0.1] * samples))


def plain(track: int, samples: int, ts: float = TS) -> bytes:
    return struct.pack("<id", track, ts) + struct.pack(f"<{samples}f", *([0.1] * samples))


def test_a_no_name_frame_is_unchanged():
    assert frame_samples(plain(0, 8000)) == 8000


def test_a_named_frame_counts_its_samples_past_the_padded_name():
    assert frame_samples(named(3, "Dmitriy Grankin", 4096)) == 4096
    assert frame_samples(named(0, "Анна", 160)) == 160            # UTF-8, padded
    assert frame_samples(named(1, "abcd", 200)) == 200            # already 4-byte aligned


def test_bad_named_frames_are_refused():
    good = named(2, "Maya", 100)
    assert frame_samples(good[:14]) is None                       # header cut
    assert frame_samples(good[:-2]) is None                       # PCM misaligned
    long_name = named(2, "x" * (MAX_NAME_BYTES + 1), 200)
    assert frame_samples(long_name) is None
    bad_utf8 = struct.pack("<Idi", 0x80000002, TS, 2) + b"\xff\xfe\0\0" + struct.pack("<f", 0.1)
    assert frame_samples(bad_utf8) is None
    assert frame_samples(named(MAX_TRACKS, "Maya", 200)) is None  # too many channels
    assert frame_parse(named(MAX_TRACKS, "Maya", 200)) == (200, MAX_TRACKS)   # parsed: the door drops it, not the note
    assert frame_samples(named(2, "Maya", MIN_NAMED_SAMPLES - 1)) is None     # names cannot dominate the bytes
    assert frame_samples(named(2, "Maya", MAX_FRAME_SAMPLES + 1)) is None
    assert frame_samples(named(2, "Maya", 200, ts=5.0)) is None   # not an epoch capture time
