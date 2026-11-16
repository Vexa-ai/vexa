"""M1 (architecture gate on L3/L4/L5, design mvp1-shared-listen §1, policy P12): one access rule.

`get_transcript_by_id` (the finished-transcript read) authorizes by `share_access.access_of`, the
same label `/link/events` and `/signal` answer by, instead of writing the a · c · b rule out again.
These tests hold the two together on a matrix of rows and callers, for the real SQL store (its
session replaced by a one-row stand-in, no Postgres) and the in-memory fake: the read is allowed
exactly when `access_of` names a label, and carries the owner tier exactly when that label is OWNER.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import textwrap
import types

import pytest

from meeting_api.collector import share_access
from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
from meeting_api.collector.fakes import InMemoryTranscriptStore

OWNER_ID, VIEWER_ID, MEMBER_ID, STRANGER_ID = 7, 8, 9, 10

ROWS = {
    "plain": {},
    "shared-with-viewer": {"transcript_viewers": [VIEWER_ID]},
    "bound-to-ws": {"workspace_id": "ws-a"},
    "bound-and-shared": {"workspace_id": "ws-a", "transcript_viewers": [VIEWER_ID]},
    "room-made": {share_access.ROOM_PRINCIPAL: {"principal": "room", "principal_workspace": "ws-a"},
                  "workspace_id": "ws-a"},
    "deleted": {"artifact_deletion": {"state": "completed"}, "transcript_viewers": [VIEWER_ID]},
    "deletion-pending": {"artifact_deletion": {"state": "pending"}},
    "workspace-empty": {"workspace_id": ""},
}
CALLERS = (OWNER_ID, VIEWER_ID, MEMBER_ID, STRANGER_ID)
WORKSPACES = (None, set(), {"ws-a"}, {"ws-b"}, {""})
CASES = list(itertools.product(sorted(ROWS), CALLERS, WORKSPACES))


def _expected(data, caller, ws):
    if share_access.deleted(data):
        return None
    return share_access.access_of(OWNER_ID, data, caller, ws)


class _Row:
    def __init__(self, data):
        self.id, self.user_id, self.data = 1, OWNER_ID, data


class _Session:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        row = self._row
        return types.SimpleNamespace(scalars=lambda: types.SimpleNamespace(first=lambda: row))


def _sql_store(data):
    store = SqlAlchemyTranscriptStore(lambda: _Session(_Row(data)))
    seen = {}

    async def pg_part(_db, _meeting):
        return {"id": 1}

    async def merge(pg, *, viewer_is_owner):
        seen["owner_tier"] = viewer_is_owner
        return dict(pg)

    store._transcript_pg_part = pg_part
    store._merge_live_segments = merge
    return store, seen


@pytest.mark.parametrize("row,caller,ws", CASES)
async def test_the_sql_read_agrees_with_access_of(row, caller, ws):
    data = dict(ROWS[row])
    store, seen = _sql_store(data)
    got = await store.get_transcript_by_id(caller, 1, ws)
    want = _expected(data, caller, ws)
    assert (got is not None) == (want is not None), (row, caller, ws, want)
    if got is not None:
        assert seen["owner_tier"] == (want == share_access.OWNER)


@pytest.mark.parametrize("row,caller,ws", CASES)
async def test_the_fake_read_agrees_with_access_of(row, caller, ws):
    data = dict(ROWS[row])
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER_ID, platform="google_meet", native_meeting_id="abc-defg-hij",
                             data=data)
    tiers = []
    real_doc = store._transcript_doc

    async def doc(m, *, viewer_is_owner):
        tiers.append(viewer_is_owner)
        return await real_doc(m, viewer_is_owner=viewer_is_owner)

    store._transcript_doc = doc
    got = await store.get_transcript_by_id(caller, mid, ws)
    want = _expected(data, caller, ws)
    assert (got is not None) == (want is not None), (row, caller, ws, want)
    if got is not None:
        assert tiers == [want == share_access.OWNER]


@pytest.mark.parametrize("fn", [SqlAlchemyTranscriptStore.get_transcript_by_id,
                                InMemoryTranscriptStore.get_transcript_by_id])
def test_the_read_does_not_write_the_rule_out_again(fn):
    """The rule's facts (the viewer list, the bound workspace) are read only inside access_of."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "access_of" in calls
    assert not consts & {"transcript_viewers", "workspace_id"}, consts & {"transcript_viewers", "workspace_id"}
