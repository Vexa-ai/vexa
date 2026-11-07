"""segment_bus — publish fixture segments onto ``transcription_segments`` the way a bot does, and read
back what the collector admitted.

The collector admits a stream entry only when the meeting's own session signed it (``auth`` + ``sig``
from a MeetingToken that names the meeting; ``meeting_api.collector.ingest``). So a tool that writes
the stream mints a token for the meeting it publishes to and signs every entry with the collector's
own signer, ``signed_entry``. Minting needs ``ADMIN_TOKEN``, so this runs where meeting-api runs:
``remote()`` returns this module's source plus one call, for ``docker exec <meeting-api> python -c``.

It writes only the raw stream. ``tc:meeting:{meeting_id}`` (keyed by the numeric meeting id) is the
collector's, and ``read_feed`` only reads it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STREAM = "transcription_segments"


def _redis(redis_url=None):
    import redis

    return redis.from_url(redis_url or os.environ.get("REDIS_URL", "redis://redis:6379/0"),
                          decode_responses=True)


def publish(lines, meeting_id, native, *, pace_s=0.0, redis_url=None) -> int:
    """XADD each JSON payload line as meeting ``meeting_id``'s session would. Returns the count."""
    from meeting_api.collector import signed_entry
    from meeting_api.meeting_token import mint_meeting_token

    r = _redis(redis_url)
    token = mint_meeting_token(int(meeting_id), 0, "google_meet", str(native),
                               session_uid=f"eval-{native}", ttl_seconds=3600)
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        r.xadd(STREAM, signed_entry(token, line))
        n += 1
        if pace_s:
            time.sleep(pace_s)
    return n


def read_feed(meeting_id, *, redis_url=None, settle_s=1.0, tries=40) -> list:
    """Wait until the collector's feed for ``meeting_id`` stops growing, then return its segments."""
    r = _redis(redis_url)
    key = f"tc:meeting:{int(meeting_id)}"
    prev = -1
    for _ in range(tries):
        cur = r.xlen(key)
        if cur > 0 and cur == prev:
            break
        prev = cur
        time.sleep(settle_s)
    out = []
    for _id, fields in r.xrange(key):
        try:
            p = json.loads(fields["payload"])
        except (KeyError, TypeError, ValueError):
            continue
        for sg in p.get("segments", []):
            out.append({"speaker": sg.get("speaker"), "text": sg.get("text")})
    return out


def clear_feed(meeting_id, *, redis_url=None) -> None:
    """Start a run from an empty feed. The key is the collector's; an eval stack is the tool's own."""
    _redis(redis_url).delete(f"tc:meeting:{int(meeting_id)}")


def remote(call: str) -> str:
    """The code ``docker exec <meeting-api> python -c`` runs: this module, then ``call``."""
    return Path(__file__).read_text(encoding="utf-8") + "\nimport sys\n" + call + "\n"
