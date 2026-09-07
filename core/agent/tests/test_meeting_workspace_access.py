"""A member of the meeting's bound workspace may open it, watch it, and annotate it (Vexa-ai/vexa#1648).

A bot requested from inside a workspace makes the WORKSPACE's meeting, so the four routes in
`routers/meetings.py` — the note (read + mint), the terms (read + publish) and the live SSE — must
answer for every member of that workspace, not only for the row's owner. They all decide access
through ONE seam, `_meeting_owner_lookup`, whose own comment used to read *"a shared-workspace
membership grant would extend `_meeting_owner_lookup` — the clean seam — but is intentionally NOT
honored here yet"*. This is that grant, and these are its tests.

Offline (fakes + tmp dirs, no docker, no runtime, no DB, no meeting-api).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane.api import create_app  # noqa: E402
from control_plane.api_shared import _http_meeting_owner_lookup  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared.config import load_settings  # noqa: E402

OWNER, MEMBER, STRANGER = "301", "302", "303"
WS, ROW = "team-notes", "5150"


class _FakeRuntime:
    def launch(self, *a, **k): raise AssertionError("no runtime in these tests")


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    """One shared workspace whose roster names OWNER and MEMBER, and a lookup that behaves like
    meeting-api's access union: the owner always, a member only when the caller's workspaces reach
    the row's binding, nobody else."""
    for uid in (OWNER, MEMBER, STRANGER):
        (tmp_path / uid).mkdir(parents=True)
    ws = tmp_path / WS
    (ws / "policy").mkdir(parents=True)
    (ws / "policy" / "members.json").write_text(json.dumps([
        {"subject": OWNER, "role": "owner", "email": "owner@example.test"},
        {"subject": MEMBER, "role": "reader", "email": "member@example.test"},
    ]))

    def _access(user_id, meeting_id, workspaces=None):
        if str(meeting_id) != ROW:
            return None
        row = {"id": ROW, "native_meeting_id": "96088138284",
               "user_id": OWNER, "data": {"workspace_id": WS}}
        if str(user_id) == OWNER:
            return row
        return row if WS in (workspaces or []) else None

    # `redis_url` is set so the SSE route reaches its ACCESS gate: without one it answers 501 before
    # deciding anything, and a test that accepted that would be asserting nothing about access. No
    # redis is ever contacted — every caller here is refused, or reads a route that needs none.
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_access,
        redis_url="redis://127.0.0.1:6379/0"))


# ── the seam itself ──────────────────────────────────────────────────────────────────────────────

def test_the_lookup_forwards_the_callers_workspaces_and_omits_them_when_there_are_none():
    """`X-User-Workspaces` is what lets meeting-api run the third branch of its access union. It is
    sent only when the caller HAS memberships: an empty header is not the same as no header, and a
    caller with none must get exactly the owner-scoped answer it got before."""
    seen: list[dict] = []

    class _Resp:
        status = 200
        def read(self): return b'{"id": "5150"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    real = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: (seen.append(dict(req.headers)), _Resp())[1]
    try:
        lookup = _http_meeting_owner_lookup("http://meeting-api")
        assert lookup(OWNER, ROW, [WS, "other"]) == {"id": "5150"}
        assert seen[-1].get("X-user-workspaces") == f"{WS},other"

        lookup(OWNER, ROW)
        assert "X-user-workspaces" not in seen[-1]
        lookup(OWNER, ROW, [])
        assert "X-user-workspaces" not in seen[-1]
        # blank entries are dropped rather than sent as a workspace named ""
        lookup(OWNER, ROW, ["", "  "])
        assert "X-user-workspaces" not in seen[-1]
    finally:
        urllib.request.urlopen = real


def test_a_row_id_that_is_not_a_row_is_refused_before_any_hop():
    """Unchanged, and it matters: this is the guard that keeps a caller from walking sequential ids."""
    lookup = _http_meeting_owner_lookup("http://meeting-api")
    assert lookup(OWNER, "not-a-number", [WS]) is None
    assert lookup("", ROW, [WS]) is None


# ── the routes ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("get", f"/api/meeting/note?meeting_id={ROW}", None),
    ("post", "/api/meeting/note", {"meeting_id": ROW}),
    ("get", f"/api/meeting/terms?meeting_id={ROW}", None),
    ("post", "/api/meeting/terms", {"meeting_id": ROW, "terms": []}),
])
def test_a_member_is_not_refused_where_only_the_owner_used_to_pass(client, method, path, body):
    """Every route that gates on the seam. A reader ("viewer") is included deliberately: the founder's
    rule is that owner and contributor see everything and a reader reads, so a reader must not be
    turned away from their own group's meeting."""
    call = getattr(client, method)
    for uid in (OWNER, MEMBER):
        r = call(path, headers={"X-User-Id": uid}, **({"json": body} if body else {}))
        assert r.status_code != 403, f"{uid} was refused {path}: {r.text}"

    refused = call(path, headers={"X-User-Id": STRANGER}, **({"json": body} if body else {}))
    assert refused.status_code == 403, f"a non-member reached {path}"


def test_the_live_transcript_refuses_a_non_member_before_the_stream_opens(client):
    """The SSE gate is the one that leaked a live transcript cross-tenant, so its refusal is the one
    that must not regress while the membership grant widens it."""
    r = client.get(f"/api/meeting/stream?meeting_id={ROW}&session_uid={ROW}",
                   headers={"X-User-Id": STRANGER})
    assert r.status_code == 403


def test_membership_comes_from_the_roster_on_disk_not_from_a_header(client):
    """`_caller_workspaces` reads `policy/members.json`, so a client cannot talk its way into a
    meeting by declaring a workspace: agent-api is reachable directly in the dev/self-host topology,
    where identity headers are spoofable, and this value decides who may read a live transcript."""
    r = client.get(f"/api/meeting/note?meeting_id={ROW}",
                   headers={"X-User-Id": STRANGER, "X-User-Workspaces": WS})
    assert r.status_code == 403
