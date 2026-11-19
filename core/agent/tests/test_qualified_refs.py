"""A file reference names its workspace, or it is not a reference (founder ruling 2026-10-18).

The bug: a chat writing to the Narrative workspace listed `README.md` beside the four files it had
written, and the chip opened the organisation's README — a bare path was looked for in workspace
after workspace, and every workspace has a README. Founder: *"we must require full paths to specific
workspaces, we do not want to guess which readme this should open"*.

Pinned here, on the server side:
  * the contract: `[[page:<workspace>/<path>]]` (`links.page_ref`), and what a bare path is;
  * a bare path is never searched for in another workspace (`link_resolver`), while a qualified one
    opens the workspace it names;
  * the agent's file list produces qualified refs: the write step records its workspace, the
    `artifact` event carries a `ref`, `workspace_write` answers one;
  * an old message is qualified only against the ONE workspace its session wrote into.
"""
from __future__ import annotations

import json

import pytest

from control_plane import link_resolver, workspace_ids as ids
from control_plane.api_shared import qualify_artifact
from control_plane.workspace_reader import _tool_op, parse_history, qualified_history
from workspaces.shared import links


# ── the contract ─────────────────────────────────────────────────────────────────────────────────

def test_the_file_reference_is_page_kind_workspace_then_path():
    ref = links.page_ref("narrative", "README.md", label="README.md")
    assert ref == "[[page:narrative/README.md|README.md]]"
    r = links.parse_universal(ref)
    assert (r.kind, r.id) == ("page", "narrative/README.md")
    with pytest.raises(ValueError):
        links.page_ref("", "README.md")


@pytest.mark.parametrize("text,bare", [
    ("README.md", True), ("kg/entities/person/jane-liu.md", True), ("notes.mdx", True),
    ("/workspaces/narrative/README.md", False), ("../x.md", False), ("package.json", False),
    ("narrative:README.md", False), ("Jane Liu", False),
])
def test_what_a_bare_path_is(text, bare):
    assert links.is_bare_path(text) is bare


def test_qualifying_rewrites_code_and_links_never_fences():
    text = ("Wrote `README.md`, `narratives.md` and [the plan](playbook.md). `package.json` stays.\n"
            "```\nls README.md `README.md`\n```")
    out = links.qualify_bare_paths(text, "narrative")
    assert "[[page:narrative/README.md|README.md]]" in out
    assert "[[page:narrative/narratives.md|narratives.md]]" in out
    assert "[[page:narrative/playbook.md|the plan]]" in out
    assert "`package.json`" in out
    assert out.endswith("```\nls README.md `README.md`\n```")     # a fence is literal
    assert links.qualify_bare_paths(text, "") == text                # no workspace, no rewrite


# ── the resolver: a bare ref never opens another workspace's file ────────────────────────────────

@pytest.fixture()
def world(tmp_path):
    """The reader's desk (57) and two workspaces they can read, each with its own README.md."""
    (tmp_path / "57" / "kg").mkdir(parents=True)
    for g, title in (("_global", "The organisation"), ("narrative", "Narrative")):
        (tmp_path / g / "policy").mkdir(parents=True)
        (tmp_path / g / "policy" / "members.json").write_text('[{"subject":"57","role":"owner"}]')
        (tmp_path / g / "README.md").write_text(f"---\ntitle: {title}\n---\n")
    reg = ids.WorkspaceRegistry()
    ids.migrate(tmp_path, reg)
    member = lambda root, slug, subject: "owner" if subject == "57" and slug in ("_global", "narrative") else None  # noqa: E731
    return tmp_path, reg, member


def _resolve(world, ref, here_slug="57"):
    root, reg, member = world
    return link_resolver.resolve(ref, subject="57", root=root, registry=reg,
                                 here=reg.by_slug(here_slug), is_member=member)


def test_a_bare_readme_is_never_found_in_another_workspace(world):
    r = _resolve(world, "README.md")                     # the desk has none
    assert r.get("slug") in (None, "57")
    assert r.get("path") is None and not r.get("ambiguous") and not r.get("candidates")
    assert "organisation" not in json.dumps(r).lower()


def test_a_bare_path_reads_only_the_workspace_it_was_written_in(world):
    r = _resolve(world, "README.md", here_slug="narrative")
    assert (r["slug"], r["path"], r["title"]) == ("narrative", "README.md", "Narrative")


def test_a_qualified_ref_opens_the_right_one(world):
    root, reg, member = world
    wid = reg.by_slug("narrative")["id"]
    r = link_resolver.resolve(f"ws:{wid}/README.md", subject="57", root=root, registry=reg,
                              here=reg.by_slug("57"), is_member=member)
    assert (r["slug"], r["path"], r["title"]) == ("narrative", "README.md", "Narrative")
    r = _resolve(world, "narrative:README.md")
    assert (r["slug"], r["path"]) == ("narrative", "README.md")


def test_an_entity_name_still_crosses_workspaces(world):
    """#1783 is about NAMES (`[[Pat Mason]]`), and stays: only paths stopped being searched."""
    root, reg, member = world
    (root / "narrative" / "kg" / "entities" / "person").mkdir(parents=True)
    (root / "narrative" / "kg" / "entities" / "person" / "pat-mason.md").write_text("---\ntitle: Pat Mason\n---\n")
    r = _resolve(world, "pat-mason")
    assert (r.get("slug"), r.get("path")) == ("narrative", "kg/entities/person/pat-mason.md")


# ── the agent's file list produces qualified refs ────────────────────────────────────────────────

def test_a_write_step_records_the_workspace_it_named():
    assert _tool_op("Write", {"file_path": "/workspaces/narrative/README.md"})["written"] == \
        {"workspace": "narrative", "path": "README.md"}
    assert _tool_op("mcp__vexa__workspace_write", {"path": "ideas.md", "slug": "narrative"})["written"] == \
        {"workspace": "narrative", "path": "ideas.md"}
    # no slug: it landed in the chat's target, which the record does not hold
    assert _tool_op("mcp__vexa__workspace_write", {"path": "ideas.md"})["written"] == \
        {"workspace": None, "path": "ideas.md"}
    assert "written" not in _tool_op("Write", {"file_path": "/tmp/scratch.md"})
    assert "written" not in _tool_op("Read", {"file_path": "/workspaces/narrative/README.md"})


def test_the_artifact_event_carries_a_qualified_ref():
    ev = {"type": "artifact", "workspace": "narrative", "path": "README.md", "focus": True}
    assert qualify_artifact(ev, target="other", desk="57") == "[[page:narrative/README.md]]"
    assert ev["ref"] == "[[page:narrative/README.md]]" and ev["focus"] is True
    # the write named no workspace: it went to the chat's target, then the desk
    assert qualify_artifact({"type": "artifact", "workspace": "", "path": "ideas.md"}, target="narrative",
                            desk="57") == "[[page:narrative/ideas.md]]"
    assert qualify_artifact({"type": "artifact", "workspace": "", "path": "x.md"}, desk="57") == "[[page:57/x.md]]"
    assert qualify_artifact({"type": "artifact", "workspace": "personal", "path": "x.md"}, desk="57") == "[[page:57/x.md]]"
    meeting = {"type": "artifact", "path": "meeting:153", "pin": True}
    assert qualify_artifact(meeting, target="narrative", desk="57") is None and "ref" not in meeting


def test_workspace_write_answers_with_the_ref():
    """`PUT /api/workspace/file` names what it wrote with the qualified ref (read off the route)."""
    import inspect
    from control_plane.routers import workspaces as ws_router
    src = inspect.getsource(ws_router)
    assert '"ref": links_mod.page_ref(slug or str(subject), rel)' in src


# ── old messages: qualified only against the session's one recorded write workspace ──────────────

def _session(*writes, reply="I wrote `README.md`, `narratives.md` and `landscape.md`."):
    lines = []
    for i, (tool, args) in enumerate(writes):
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": args}]}}))
        lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]}}))
    lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": reply}]}}))
    first = json.dumps({"type": "user", "message": {"role": "user", "content": "Build the narrative"}})
    return parse_history("\n".join([first, *lines]), [])


def test_an_old_message_in_a_one_workspace_session_is_qualified_against_it():
    turns = qualified_history(_session(
        ("Write", {"file_path": "/workspaces/narrative/README.md", "content": "x"}),
        ("mcp__vexa__workspace_write", {"path": "narratives.md", "slug": "narrative", "content": "x"}),
    ), "57")
    agent = [t for t in turns if t["role"] == "agent"]
    text = " ".join(t["text"] for t in agent)
    assert "[[page:narrative/README.md|README.md]]" in text
    assert "[[page:narrative/landscape.md|landscape.md]]" in text
    refs = [op.get("ref") for t in agent for op in t.get("ops") or [] if op.get("written")]
    assert refs == ["[[page:narrative/README.md]]", "[[page:narrative/narratives.md]]"]


@pytest.mark.parametrize("writes", [
    (),                                                                             # wrote nowhere
    (("Write", {"file_path": "/workspaces/narrative/README.md"}),
     ("Write", {"file_path": "/workspaces/_global/README.md"})),                    # two workspaces
    (("mcp__vexa__workspace_write", {"path": "README.md"}),),                       # target not recorded
])
def test_otherwise_an_old_message_is_left_plain(writes):
    turns = qualified_history(_session(*writes), "57")
    text = " ".join(t["text"] for t in turns if t["role"] == "agent")
    assert "[[page:" not in text and "`README.md`" in text
