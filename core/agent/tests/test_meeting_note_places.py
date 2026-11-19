"""A MEETING'S NOTE IS FOUND BY ITS MEETING, WHEREVER IT LIVES (#1783).

Founder, 2026-10-15, on app.dev: he finalized a meeting's report, deleted his Personal workspace,
and the meeting sidebar said "No notes for this meeting yet". The report was still in the Personal
workspace's directory; his chat now works in another workspace, which had become his primary, so
the sidebar read the path there and found nothing. These pin: the note is looked for in the home
(the primary), the other active workspaces, then a switched-off Personal workspace (read-only, with
its text); nothing new is written into a switched-off Personal; and a report somewhere is never
shadowed by a fresh empty page.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from control_plane import meeting_mint, meeting_note
from shared import meeting_doc

SUBJECT = "2"
NOTE = "kg/entities/meeting/2026-10-15-0900-google-meet-meeting-jmk-qxpo-yzg.md"
ROW = {"id": 120, "native_meeting_id": "jmk-qxpo-yzg", "platform": "google_meet",
       "start_time": "2026-10-15T09:00:00+00:00",
       "data": {"metadata": {"note_path": NOTE}, "title": "Vexum logo review"}}
REPORT = (meeting_doc.scaffold(meeting="120", title="Vexum logo review", native="jmk-qxpo-yzg", date="2026-10-15")
          .replace("transcript_cursor:", "transcript_cursor: 664")
          + "\n## Report\n<!-- meeting:report:start -->\n[[Dmitry Grankin]] asked [[Tatiana Sukhova]] for the logo.\n"
            "<!-- meeting:report:end -->\n")


def _world(tmp_path, monkeypatch, personal_active: bool):
    root = tmp_path / "workspaces"
    desk = root / SUBJECT
    biz = root / ".attached" / SUBJECT / "biz-b1c87f"
    for d in (desk, biz):
        d.mkdir(parents=True)
    mounts = ([SimpleNamespace(slug="seed", path=str(desk), primary=True, write=True, desk=True)] if personal_active else []) + [
        SimpleNamespace(slug="biz-b1c87f", path=str(biz), primary=not personal_active, write=True, desk=False)]
    import control_plane.workspace_attach as wa
    monkeypatch.setattr(wa, "active_workspaces", lambda _root, _subject: list(mounts))
    # "switched off" is HIDDEN (one name per id: an inactive Personal is still the desk)
    store = root / ".attached" / SUBJECT
    store.mkdir(parents=True, exist_ok=True)
    import json as _json
    (store / "state.json").write_text(_json.dumps({"_flat_v1": True, "active": None,
        "active_set": (["seed"] if personal_active else []) + ["biz-b1c87f"],
        "hidden": [] if personal_active else ["seed"],
        "slots": {"seed": {}, "biz-b1c87f": {"name": "biz"}}}))
    return root, desk, biz


def _put(d: Path, rel: str, text: str) -> None:
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    (d / rel).write_text(text)


def test_a_report_in_a_switched_off_personal_workspace_is_found_and_shown_read_only(tmp_path, monkeypatch):
    root, desk, _biz = _world(tmp_path, monkeypatch, personal_active=False)
    _put(desk, NOTE, REPORT)
    out = meeting_note.describe(root, SUBJECT, ROW)
    assert out["path"] == NOTE
    assert out["readonly"] is True
    assert "[[Tatiana Sukhova]]" in out["content"]      # its text, since no read route reaches it
    assert out["cursor"] == "664"


def test_a_hidden_personal_leaves_no_home_and_its_dir_is_read_only(tmp_path, monkeypatch):
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=False)
    with pytest.raises(Exception):
        meeting_note.home_dir(root, SUBJECT)
    places = meeting_note.note_places(root, SUBJECT)
    assert [(p["slug"], p["home"], p["hidden"]) for p in places] == [("biz-b1c87f", False, False), ("", False, True)]


def test_nothing_new_is_written_anywhere_when_personal_is_hidden_and_a_report_is_never_shadowed(tmp_path, monkeypatch):
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=False)
    _put(desk, NOTE, REPORT)
    out = meeting_mint.mint(root, SUBJECT, ROW)
    assert out["created"] is False and out["path"] == NOTE and out.get("readonly") is True
    assert not (biz / NOTE).exists()
    other = dict(ROW, id=121, native_meeting_id="abc-defg-hij", data={"title": "Another"})
    made = meeting_mint.mint(root, SUBJECT, other)
    assert made["path"] is None and made["refused"] == "no workspace to write to"
    assert not (biz / "kg").exists() and not (desk / "kg/entities/meeting/x").exists()


def test_a_note_in_the_home_is_read_where_the_routes_read_by_default(tmp_path, monkeypatch):
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=False)
    _put(biz, NOTE, REPORT)
    out = meeting_note.describe(root, SUBJECT, ROW)
    assert out["path"] == NOTE and out["workspace"] == "biz-b1c87f" and "content" not in out


def test_with_personal_active_nothing_changes(tmp_path, monkeypatch):
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=True)
    _put(desk, NOTE, REPORT)
    out = meeting_note.describe(root, SUBJECT, ROW)
    assert out["path"] == NOTE and out["workspace"] == "" and "readonly" not in out
    assert meeting_note.home_dir(root, SUBJECT) == desk
    assert meeting_note.desk_holds_note(root, SUBJECT, NOTE)


def test_a_note_in_another_active_workspace_names_it(tmp_path, monkeypatch):
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=True)
    _put(biz, NOTE, REPORT)
    out = meeting_note.describe(root, SUBJECT, ROW)
    assert out["path"] == NOTE and out["workspace"] == "biz-b1c87f"


@pytest.mark.parametrize("target", ["", "biz"])
def test_the_worker_writes_notes_to_the_target_or_personal_never_by_order(target, monkeypatch):
    from worker.engine import _note_home
    monkeypatch.setenv("VEXA_OWNER", SUBJECT)
    monkeypatch.setenv("VEXA_TARGET_WORKSPACE", target)
    mounts = [{"slug": "shared-1", "path": "/workspaces/shared-1", "write": True, "primary": True, "desk": False},
              {"slug": "biz", "path": f"/workspaces/.attached/{SUBJECT}/biz", "write": True, "primary": False, "desk": False},
              {"slug": "_global", "path": "/workspaces/_global", "write": False, "role": "global"}]
    got = _note_home(mounts)
    assert (str(got) if got else None) == (f"/workspaces/.attached/{SUBJECT}/biz" if target else None)
    mounts.append({"slug": "seed", "path": f"/workspaces/{SUBJECT}", "write": True, "primary": False, "desk": True})
    monkeypatch.setenv("VEXA_TARGET_WORKSPACE", "")
    assert str(_note_home(mounts)) == f"/workspaces/{SUBJECT}"


def test_a_page_that_names_the_meeting_is_adopted_and_recorded(tmp_path, monkeypatch):
    """An agenda page written before the call, naming the meeting by its native id, IS its note: the
    bot's mint creates no second page, and records the agenda page so the report lands in it."""
    root, desk, biz = _world(tmp_path, monkeypatch, personal_active=True)
    agenda = "kg/entities/meeting/vexa-channel-call-15-oct-2026.md"
    _put(desk, agenda, "---\ntype: meeting\nnative: jmk-qxpo-yzg\ntitle: Vexa Channel call\n---\n\n## Agenda\n- pilot\n")
    recorded = []
    row = dict(ROW, data={"title": ""})                      # a bot row: no title, no recorded path
    out = meeting_mint.mint(root, SUBJECT, row, record=lambda s, mid, rel: recorded.append(rel))
    assert out == {"path": agenda, "created": False}
    assert recorded == [agenda]
    assert [p.name for p in (desk / "kg/entities/meeting").iterdir()] == ["vexa-channel-call-15-oct-2026.md"]