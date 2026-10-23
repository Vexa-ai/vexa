"""The watcher's meeting-doc call names only that meeting's docs.

The platform and native meeting id come from the meeting list, which a bot fills in. Each must be
one plain path segment, and is percent-encoded into the gateway URL the watcher calls with the
deployment's bot key; anything else is not called at all.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from control_plane import transcription_watcher as tw


@pytest.fixture
def calls(monkeypatch):
    seen = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        seen.append(req.full_url)
        return _Resp()

    monkeypatch.setenv("VEXA_BOT_API_KEY", "fixture-bot-key")
    monkeypatch.setenv("VEXA_GATEWAY_URL", "http://gateway:8000")
    monkeypatch.setattr(tw.urllib.request, "urlopen", urlopen)
    return seen


@pytest.mark.parametrize("native,platform", [
    ("../../admin/users", "google_meet"),
    ("..", "google_meet"),
    ("abc/def", "google_meet"),
    ("abc-defg-hij", "../internal"),
    (".hidden", "google_meet"),
])
def test_an_id_that_is_not_one_plain_segment_is_not_called(calls, native, platform):
    tw._record_meeting_doc(native, platform, "u1")
    assert calls == []


def test_a_plain_id_is_encoded_into_its_own_segment(calls):
    tw._record_meeting_doc("abc?x=1#y", "google_meet", "u1")
    (url,) = calls
    assert urlsplit(url).path == "/meetings/google_meet/abc%3Fx%3D1%23y/docs"
    assert urlsplit(url).query == "" and urlsplit(url).fragment == ""


def test_an_ordinary_meeting_id_is_unchanged(calls):
    tw._record_meeting_doc("abc-defg-hij", "google_meet", "u1")
    assert calls == ["http://gateway:8000/meetings/google_meet/abc-defg-hij/docs"]
