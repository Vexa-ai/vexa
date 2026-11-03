"""A forget reaches the stored transcript: every version, the title and posted notes, the matched
spans redacted and each turn kept (Vexa-ai/vexa#1783). Values are made up."""
from __future__ import annotations

import json

from tests._session_fakes import MemBlobs
from control_plane.session_store import SessionStore
from shared import sensitive

TURN = {"type": "user", "message": {"content": "Read the POA. Passport no. AB1234567."}}


def test_every_version_is_redacted_in_place_and_the_turns_are_kept(tmp_path):
    blobs = MemBlobs()
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", blobs)
    sess = s.create("47", title="POA with AB1234567")
    sid = sess["id"]
    body = (json.dumps(TURN) + "\n" + json.dumps({"type": "assistant", "message": {"content": "ok"}}) + "\n").encode()
    v1 = s.save_bundle(sid, 0, {".claude/projects/x/t.jsonl": body})
    v2 = s.save_bundle(sid, v1, {".claude/projects/x/t.jsonl": body + b'{"type":"user"}\n'})
    s.add_note(sid, "47", "the number is AB1234567")
    item = sensitive.values(TURN["message"]["content"])      # what the attachment itself carries
    out = s.redact_owned("47", sensitive.redactor(literals=item, identifiers=True))
    assert out["sessions"] == 1 and out["spans"] >= 4
    for v in (v1, v2):
        text = s.load_bundle(sid, v)["files"][".claude/projects/x/t.jsonl"].decode()
        assert "AB1234567" not in text and text.count("\n") >= 2
    assert "AB1234567" not in s.get(sid)["title"]
    assert all("AB1234567" not in n["text"] for n in s.notes(sid))
    assert all(b"AB1234567" not in v for v in blobs._d.values()) if hasattr(blobs, "_d") else True
