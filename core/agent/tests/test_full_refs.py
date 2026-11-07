"""FULL REFERENCES, RENDERED AS BADGES (founder 2026-10-18: "Chat about this" put
`About the meeting "Call with Vexa":` in the composer and he has many meetings by that name — "this
reference is ambiguous"; then "we need full reference for everything when we reference, rendered as
badge (not string uuid)").

The resolver answers each reference with the object's own name AND a disambiguator for the badge (a
meeting's time and owner, a page's workspace, a person's role, a commit's short hash) — never an id as
a name. A message that references a meeting is grounded on EXACTLY that meeting, per viewer."""
from __future__ import annotations

from control_plane import universal_refs as refs_mod
from control_plane.api import ChatBody, _context_grounding
from control_plane.api_shared import _referenced_meetings_block

ROWS = {
    201: {"id": 201, "status": "completed", "platform": "google_meet", "native_meeting_id": "aaa-bbbb-ccc",
          "start_time": "2026-10-11T09:00:00Z", "data": {"title": "Call with Vexa", "owner_name": "Dmitry",
                                                          "participants": [{"name": "Ana"}]}},
    202: {"id": 202, "status": "completed", "platform": "google_meet", "native_meeting_id": "ddd-eeee-fff",
          "start_time": "2026-10-18T15:26:00Z", "data": {"title": "Call with Vexa", "owner_name": "Dmitry",
                                                          "participants": [{"name": "Bruno"}]}},
    203: {"id": 203, "status": "completed", "platform": "zoom", "native_meeting_id": "9988776655",
          "start_time": "2026-10-17T08:00:00Z", "data": {}},
}


def _seams(readable=(201, 202, 203), **extra):
    def meeting(mid):
        return ROWS.get(mid) if mid in readable else None

    def page(wid, target):
        if wid == "narrative":
            return {"access": "readable", "title": target.rsplit("/", 1)[-1].removesuffix(".md").title(),
                    "path": target, "slug": "narrative", "workspace": "Narrative"}
        return {"access": "not-yours", "title": "x", "workspace": None}

    return refs_mod.Seams(workspace=lambda w: (None, "gone"), page=page, chat=lambda c: (None, False),
                          meeting=meeting, **extra)


def test_two_meetings_with_one_title_read_apart_by_their_hint():
    a = refs_mod.resolve("[[meeting:201]]", _seams())
    b = refs_mod.resolve("[[meeting:202]]", _seams())
    assert a["label"] == b["label"] == "Call with Vexa"
    assert a["hint"]["at"] == "2026-10-11T09:00:00Z" and b["hint"]["at"] == "2026-10-18T15:26:00Z"
    assert a["hint"]["by"] == "Dmitry"
    assert b["open"] == {"meeting": 202}


def test_an_untitled_meeting_is_never_named_by_its_id():
    got = refs_mod.resolve("[[meeting:203]]", _seams())
    assert got["label"] == "Untitled meeting"
    assert "9988776655" not in str(got["label"]) and "203" not in str(got["label"])
    assert got["hint"]["at"] == "2026-10-17T08:00:00Z"


def test_a_meeting_the_viewer_cannot_read_carries_no_hint():
    got = refs_mod.resolve("[[meeting:202]]", _seams(readable=()))
    assert got["access"] == "unavailable" and got["label"] is None and "hint" not in got


def test_page_and_policy_say_their_workspace_and_hide_it_from_a_stranger():
    page = refs_mod.resolve("[[page:narrative/kg/plan.md]]", _seams())
    assert page["label"] == "Plan" and page["hint"] == {"workspace": "Narrative"}
    policy = refs_mod.resolve("[[policy:narrative/policy/sharing.md]]", _seams())
    assert policy["kind"] == "policy" and policy["hint"] == {"workspace": "Narrative"}
    stranger = refs_mod.resolve("[[page:elsewhere/kg/plan.md]]", _seams())
    assert stranger["access"] == "not-yours" and stranger["label"] is None and "hint" not in stranger


def test_a_commit_is_its_message_and_short_hash_and_opens_its_review():
    seen = []

    def commit(wid, sha):
        seen.append((wid, sha))
        return {"sha": "abc1234def5678", "subject": "Update the plan", "at": "2026-10-18T10:00:00Z"}, "readable"

    got = refs_mod.resolve("[[commit:narrative/abc1234def5678]]", _seams(commit=commit))
    assert seen == [("narrative", "abc1234def5678")]
    assert got["label"] == "Update the plan" and got["hint"]["sha"] == "abc1234"
    assert got["open"] == {"commit": "abc1234def5678", "slug": "narrative"}
    # not a sha, or no way to read one: nothing named
    assert refs_mod.resolve("[[commit:narrative/not-a-sha]]", _seams(commit=commit))["access"] == "gone"
    assert refs_mod.resolve("[[commit:narrative/abc1234]]", _seams())["access"] == "unavailable"
    theirs = refs_mod.resolve("[[commit:secret/abc1234]]", _seams(commit=lambda w, s: (None, "not-yours")))
    assert theirs["access"] == "not-yours" and theirs["label"] is None


def test_a_referenced_meeting_grounds_on_exactly_that_meeting():
    access = lambda mid: ROWS.get(int(mid)) if mid.isdigit() and int(mid) in (201, 202) else None  # noqa: E731
    block = _referenced_meetings_block("[[meeting:202]] what was decided?", access, None)
    assert "[[meeting:202]] — Call with Vexa" in block
    assert "2026-10-18T15:26:00Z" in block and "Bruno" in block
    # the other meeting with the same title is not in it
    assert "2026-10-11" not in block and "Ana" not in block and "meeting:201" not in block


def test_a_referenced_meeting_the_viewer_cannot_read_folds_nothing():
    block = _referenced_meetings_block("about [[meeting:202]]", lambda mid: None, None)
    assert "Not available to this person" in block and "Call with Vexa" not in block


def test_the_turn_prompt_still_ends_with_the_persons_words():
    access = lambda mid: ROWS.get(int(mid)) if mid.isdigit() else None  # noqa: E731
    body = ChatBody(prompt="[[meeting:202]]\n\nwhat was decided?")
    _ctx, _tools, prompt = _context_grounding(body, "s1", None, schedule_rows=lambda: [],
                                              workspace_mounts=lambda: [], meeting_access=access)
    assert prompt.endswith(body.prompt)
    assert "Meetings referenced in this message" in prompt and "2026-10-18T15:26:00Z" in prompt
    # no reference, no block
    _c, _t, plain = _context_grounding(ChatBody(prompt="hello"), "s1", None, schedule_rows=lambda: [],
                                       workspace_mounts=lambda: [], meeting_access=access)
    assert plain == "hello"


def test_the_agent_is_told_to_write_every_object_as_a_full_reference():
    from worker import engine

    rule = engine.kg_links_preamble()
    for form in ("[[meeting:<row id>]]", "[[page:<workspace-slug>/<path>]]", "[[person:<email>]]",
                 "[[commit:<workspace-slug>/<sha>]]", "[[policy:<workspace-slug>/<path>]]"):
        assert form in rule
    assert "never a bare title" in rule
    assert "never write `|<title>` into a reference" in rule


def test_git_commit_at_reads_one_commit_subject(tmp_path):
    from control_plane.workspace_reader import WorkspaceReader
    from shared.gitexec import run_git

    ws = tmp_path / "narrative"
    ws.mkdir()
    run_git(ws, "init", "-q", check=True)
    (ws / "plan.md").write_text("# Plan\n")
    run_git(ws, "add", "plan.md", check=True)
    run_git(ws, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "Update the plan", check=True)
    sha = run_git(ws, "rev-parse", "HEAD").stdout.strip()
    wsr = WorkspaceReader(str(tmp_path))
    got = wsr.git_commit_at(ws, sha[:8])
    assert got["sha"] == sha and got["subject"] == "Update the plan" and got["at"]
    assert wsr.git_commit_at(ws, "deadbeef") is None
    assert wsr.git_commit_at(ws, "not-hex") is None
