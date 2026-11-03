"""A call's changes wait for its owner (ADR-0048 decision 7).

After a call held in a session, `post_meeting` lands nothing in a workspace on its own: the report
becomes a proposed page on the owner's desk and each commitment a proposed commitment, recorded in
the call's session, and `drop_to_attendees` skips the call. Every other meeting is untouched.
No network: the meeting row and agent-api are fakes.
"""
from __future__ import annotations

import flows_defs.production as production
from flows import Done, Registry

from test_attendee_drop import PRIOR, REFS, Store, _ctx, _rig

SID = "s-0123456789abcdef0123456789abcdef"


def _call(monkeypatch, *, session=SID):
    store = Store()
    reg = _rig(monkeypatch, store)
    rows = {"id": 97, "platform": "vexa_call", "data": {"call": {"session_id": session}}}
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, mid, native=None: rows)
    proposed = []
    monkeypatch.setattr(production.ag, "propose_session_change",
                        lambda uid, sid, **kw: proposed.append((uid, sid, kw)) or {"id": "p-1"})
    return reg, store, proposed


def test_a_calls_report_is_proposed_to_its_owner_and_dropped_nowhere(monkeypatch):
    reg, store, proposed = _call(monkeypatch)
    refs = {**REFS, "platform": "vexa_call"}
    out = reg.steps["propose_call_changes"](_ctx(dict(refs), PRIOR))
    assert isinstance(out, Done) and out.result["session"] == SID and out.result["proposed"] == 2
    page = [p for p in proposed if p[2]["kind"] == "page"][0]
    assert page[0] == "7" and page[1] == SID
    assert page[2]["workspace"] == "7" and page[2]["path"].startswith("kg/entities/meeting/")
    assert "ship it on the 21st" in page[2]["body"]
    commits = [p[2]["body"] for p in proposed if p[2]["kind"] == "commitment"]
    assert commits == ["Ben — the migration doc"]
    drop = reg.steps["drop_to_attendees"](_ctx(dict(refs), PRIOR))
    assert drop.result["dropped"] == 0 and "owner" in drop.result["skipped"]
    assert store.writes == []


def test_any_other_meeting_skips_the_proposals_and_is_dropped_as_before(monkeypatch):
    reg, store, proposed = _call(monkeypatch)
    out = reg.steps["propose_call_changes"](_ctx(dict(REFS), PRIOR))
    assert out.result.get("skipped") and proposed == []
    drop = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert drop.result["dropped"] == 3


def test_a_call_with_no_session_is_dropped_as_before(monkeypatch):
    reg, store, proposed = _call(monkeypatch, session="")
    refs = {**REFS, "platform": "vexa_call"}
    assert reg.steps["propose_call_changes"](_ctx(dict(refs), PRIOR)).result.get("skipped")
    assert reg.steps["drop_to_attendees"](_ctx(dict(refs), PRIOR)).result["dropped"] == 3
