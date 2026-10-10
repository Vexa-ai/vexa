"""Every work-tree read and write agent-api and the worker make as root is nofollow — one test a site.

agent-api and the worker run as root over trees the model's tools (an unprivileged user) can write:
a desk, `_system`, `_global`, a shared workspace, the Codex home beside the runtime's. A plain open
follows a link at the leaf AND at every directory on the way, so a tools user who plants one makes
root read another tenant's file into a response or a prompt, or write, overwrite or delete wherever
the link points. Each test below plants such a link (at a leaf, or at a directory on the way, or
swapped in between a check and the act), drives one site, and asserts the file outside the tree was
neither read into the result nor written, replaced or removed. Each one fails on the code before
these sites went through ``workspace_paths``, which opened the path by name.

The gate that keeps a new site from opening a work-tree path by name is
``test_worktree_io_single_path.py``.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

SECRET = "SECRET-OUTSIDE-THE-TREE"


@pytest.fixture
def outside(tmp_path):
    """A directory OUTSIDE every tree under test, holding what a planted link would reach."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "secret.md").write_text(f"---\ntype: person\nid: eve\ntitle: {SECRET}\nself: true\n"
                                 f"scheduled_at: 2026-10-10T10:00:00Z\n---\n# {SECRET}\n")
    return d


def _link(at: Path, to: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(to, at)


def _snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def _git_init(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for args in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    return d


# ── worker: the job register ─────────────────────────────────────────────────────────────────────

def _victim_jobs(outside: Path) -> Path:
    (outside / "jobs").mkdir()
    rec = outside / "jobs" / "j1.json"
    rec.write_text(json.dumps({"job_id": "j1", "kind": "research", "target": SECRET, "session": ""}))
    return rec


def test_job_register_behind_a_linked_dir_is_not_read_reported_or_cleared(tmp_path, outside):
    from worker.jobs import JobRunner
    victim = _victim_jobs(outside)
    sysd = tmp_path / "sys"
    _link(sysd / ".claude" / "jobs", outside / "jobs")
    seen: list = []
    runner = JobRunner(emit=seen.append, turn=lambda brief: iter(()),
                       register_dir=sysd / ".claude" / "jobs")
    assert runner.cancelled_at_boot() == [] and seen == []
    assert victim.exists()                                   # not deleted through the link
    runner._note("j2", "research", "t")
    assert not (outside / "jobs" / "j2.json").exists()       # not written through the link


def test_job_register_under_a_linked_claude_is_refused_from_the_root(tmp_path, outside):
    from worker.jobs import JobRunner
    victim = _victim_jobs(outside)
    sysd = tmp_path / "sys"
    sysd.mkdir()
    _link(sysd / ".claude", outside)
    seen: list = []
    runner = JobRunner(emit=seen.append, turn=lambda brief: iter(()),
                       register_dir=sysd / ".claude" / "jobs", register_root=sysd)
    assert runner.cancelled_at_boot() == [] and seen == [] and victim.exists()


def test_a_linked_job_record_is_not_read(tmp_path, outside):
    from worker.jobs import JobRunner
    victim = _victim_jobs(outside)
    reg = tmp_path / "sys" / ".claude" / "jobs"
    reg.mkdir(parents=True)
    _link(reg / "j1.json", victim)
    seen: list = []
    JobRunner(emit=seen.append, turn=lambda brief: iter(()), register_dir=reg).cancelled_at_boot()
    assert SECRET not in json.dumps(seen) and victim.exists()


# ── agent-api: attach, touches, sync ─────────────────────────────────────────────────────────────

def test_carry_policy_neither_reads_nor_writes_through_a_linked_policy(tmp_path, outside):
    from control_plane.workspace_attach import carry_policy
    (outside / "members.json").write_text(json.dumps([{"subject": "u_eve", "role": "owner"}]))
    src, dest = tmp_path / "src", tmp_path / "dest"
    dest.mkdir()
    _link(src / "policy", outside)
    assert carry_policy(src, dest) == []
    assert not (dest / "policy").exists()                    # another tree's list not carried in
    src2, dest2 = tmp_path / "src2", tmp_path / "dest2"
    (src2 / "policy").mkdir(parents=True)
    (src2 / "policy" / "members.json").write_text("[]")
    (outside / "pol").mkdir()
    dest2.mkdir()
    _link(dest2 / "policy", outside / "pol")
    with contextlib.suppress(OSError, ValueError):
        carry_policy(src2, dest2)
    assert not (outside / "pol" / "members.json").exists()   # this one's list not carried out


def test_touch_mirror_is_not_written_through_a_linked_vexa_or_git_info(tmp_path, outside):
    from control_plane.workspace_ids import mirror_touches
    desk = tmp_path / "desk"
    desk.mkdir()
    _link(desk / ".vexa", outside)
    mirror_touches(desk, [{"workspace": "w", "path": "p", "at": 1}])
    assert not (outside / "touches.json").exists()
    desk2 = tmp_path / "desk2"
    (desk2 / ".git").mkdir(parents=True)
    (outside / "info").mkdir()
    _link(desk2 / ".git" / "info", outside / "info")
    mirror_touches(desk2, [{"workspace": "w", "path": "p", "at": 1}])
    assert not (outside / "info" / "exclude").exists()


def test_sync_does_not_write_the_identity_through_a_linked_vexa(tmp_path, outside):
    from control_plane import workspace_ids as ids
    root = tmp_path / "root"
    ws = root / "desk1"
    ws.mkdir(parents=True)
    _link(ws / ".vexa", outside)
    with contextlib.suppress(OSError, ValueError):
        ids.sync_workspace(root, "desk1", registry=ids.WorkspaceRegistry())
    assert not (outside / "workspace.json").exists()


def test_a_linked_policy_does_not_classify_a_desk_as_a_group(tmp_path, outside):
    from control_plane import workspace_ids as ids
    (outside / "members.json").write_text("[]")
    ws = tmp_path / "desk"
    ws.mkdir()
    _link(ws / "policy", outside)
    assert ids.classify(ws) == "desk"


# ── agent-api: routines ──────────────────────────────────────────────────────────────────────────

_ROUTINE = f"---\nenabled: true\ncron: '0 9 * * *'\nprompt: {SECRET}\n---\nbody\n"


def test_a_linked_routine_file_is_not_loaded_or_rewritten(tmp_path, outside):
    from control_plane import workspace_routines as wr
    (outside / "r.md").write_text(_ROUTINE)
    root = tmp_path / "workspaces"
    _link(root / "u1" / "routines" / "daily.md", outside / "r.md")
    assert wr.load_routine_file(root / "u1" / "routines" / "daily.md") is None
    with contextlib.suppress(OSError, ValueError):
        wr.set_routine_file_enabled("u1", "daily", enabled=False, workspaces_dir=root)
    assert (outside / "r.md").read_text() == _ROUTINE


def test_routines_behind_a_linked_dir_are_not_listed(tmp_path, outside):
    from control_plane import workspace_routines as wr
    (outside / "r.md").write_text(_ROUTINE)
    root = tmp_path / "workspaces"
    (root / "u1").mkdir(parents=True)
    _link(root / "u1" / "routines", outside)
    cards = wr.routine_cards_for_subject("u1", jobs=[], workspaces_dir=root)
    assert SECRET not in json.dumps(cards, default=str)


# ── agent-api: reset ─────────────────────────────────────────────────────────────────────────────

def test_reset_does_not_write_the_seed_through_a_linked_child(monkeypatch, tmp_path, outside):
    from tests.test_delegation_ceiling_verbs import PERSON, _build, _person
    seeds = tmp_path / "seeds"
    (seeds / "default" / "kg").mkdir(parents=True)
    (seeds / "default" / "kg" / "seeded.md").write_text("seed\n")
    (seeds / "default" / "CLAUDE.md").write_text("seed\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEEDS_DIR", str(seeds))
    client = _build(monkeypatch, tmp_path)
    r = client.put("/api/workspace/file", json={"path": "notes.md", "content": "mine"}, headers=_person())
    assert r.status_code == 200, r.text
    desk = tmp_path / "workspaces" / PERSON
    _link(desk / "kg", outside)
    before = _snapshot(outside)
    r = client.post("/api/workspace/reset", json={"target": "personal"}, headers=_person())
    assert _snapshot(outside) == before                      # nothing written or removed outside
    assert not (desk / "kg").is_symlink()


# ── agent-api: meeting map, claims, note, mint ───────────────────────────────────────────────────

def test_meeting_terms_neither_read_nor_written_through_a_linked_vexa(tmp_path, outside):
    from control_plane import meeting_terms
    (outside / "meeting-terms").mkdir()
    victim = outside / "meeting-terms" / "m1.json"
    victim.write_text(json.dumps({"meeting": "m1", "cursor": "c",
                                  "terms": [{"term": SECRET, "segments": []}]}))
    root = tmp_path / "workspaces"
    (root / "u1").mkdir(parents=True)
    _link(root / "u1" / ".vexa", outside)
    assert SECRET not in json.dumps(meeting_terms.read(root, "u1", "m1"))
    before = victim.read_text()
    meeting_terms.extend(root, "u1", "m1", [{"term": "Acme", "segments": [1]}], cursor="c2")
    assert victim.read_text() == before


def test_claims_book_is_neither_read_nor_written_through_a_linked_folder(tmp_path, outside):
    from control_plane import claims
    victim = outside / "claims.json"
    victim.write_text(json.dumps({"claims": [{"id": "c001", "claim": SECRET, "state": "validated"}]}))
    desk = tmp_path / "desk"
    desk.mkdir()
    _link(desk / "_pending", outside)
    before = victim.read_text()
    out = None
    with contextlib.suppress(OSError, ValueError):
        out = claims.propose(desk, ["we sell widgets"])
    assert victim.read_text() == before
    assert out is None or SECRET not in json.dumps(out)


def test_meeting_note_is_not_found_through_a_link(tmp_path, outside):
    from control_plane import meeting_note
    root = tmp_path / "workspaces"
    (outside / "m.md").write_text("---\ntype: meeting\nmeeting: 147\n---\n")
    _link(root / "u1" / "kg" / "entities" / "meeting" / "report.md", outside / "m.md")
    row = {"id": 147, "native_meeting_id": "n", "platform": "google_meet",
           "data": {"note_path": "kg/entities/meeting/report.md"}}
    assert meeting_note.resolve(root, "u1", row) is None
    root2 = tmp_path / "workspaces2"
    (root2 / "u1" / "kg" / "entities").mkdir(parents=True)
    (outside / "meeting").mkdir()
    (outside / "meeting" / "x.md").write_text("---\ntype: meeting\nmeeting: 147\n---\n")
    _link(root2 / "u1" / "kg" / "entities" / "meeting", outside / "meeting")
    assert meeting_note.resolve(root2, "u1", {"id": 147, "native_meeting_id": "n", "data": {}}) is None


def test_mint_does_not_write_the_page_through_a_linked_folder(tmp_path, outside):
    from control_plane import meeting_mint
    root = tmp_path / "workspaces"
    (root / "u1" / "kg" / "entities").mkdir(parents=True)
    _link(root / "u1" / "kg" / "entities" / "meeting", outside)
    row = {"id": 118, "native_meeting_id": "cqb-egsq-vmt", "platform": "google_meet",
           "created_at": "2026-09-06T12:50:00+00:00", "data": {}}
    before = _snapshot(outside)
    with contextlib.suppress(OSError, ValueError):
        meeting_mint.mint(root, "u1", row)
    assert _snapshot(outside) == before


# ── agent-api: the front page (people, rosters) ──────────────────────────────────────────────────

def test_a_linked_policy_folder_does_not_put_its_roster_on_the_front_page(tmp_path, outside):
    from control_plane import front_page
    (outside / "members.json").write_text(json.dumps([{"subject": "u_eve", "email": "eve@x.io",
                                                       "name": SECRET, "role": "owner"}]))
    root = tmp_path / "workspaces"
    (root / "shared1").mkdir(parents=True)
    _link(root / "shared1" / "policy", outside)
    assert front_page.subject_for_address(root, "eve@x.io") is None
    assert front_page.person_name(root, "u_eve") != SECRET


def test_a_linked_workspace_dir_does_not_put_its_roster_on_the_front_page(tmp_path, outside):
    from control_plane import front_page
    (outside / "policy").mkdir()
    (outside / "policy" / "members.json").write_text(json.dumps([{"subject": "u_eve",
                                                                  "email": "eve@x.io"}]))
    root = tmp_path / "workspaces"
    root.mkdir()
    _link(root / "planted", outside)
    assert front_page.subject_for_address(root, "eve@x.io") is None


def test_a_linked_person_page_does_not_name_anyone(tmp_path, outside):
    from control_plane import front_page
    root = tmp_path / "workspaces"
    _link(root / "u1" / "kg" / "entities" / "person" / "me.md", outside / "secret.md")
    _link(root / "_global" / "kg" / "entities" / "person" / "u1.md", outside / "secret.md")
    (outside / "identity.md").write_text(f"- **name:** {SECRET}\n")
    _link(root / ".system" / "u1" / "identity.md", outside / "identity.md")
    assert front_page.person_name(root, "u1") != SECRET


# ── agent-api: presets, scaffolds, proposals, desk README and Now ────────────────────────────────

def test_preset_top_up_does_not_write_through_a_planted_link(tmp_path, outside):
    from control_plane import preset_library
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "onboard.md").write_text("preset\n")
    g = tmp_path / "global"
    (g / "asks").mkdir(parents=True)
    _link(g / "asks" / "onboard.md", outside / "dangling.md")   # dangling: "absent" by name
    preset_library.top_up(g, lib)
    assert not (outside / "dangling.md").exists()
    g2 = tmp_path / "global2"
    g2.mkdir()
    _link(g2 / "asks", outside)
    preset_library.top_up(g2, lib)
    assert not (outside / "onboard.md").exists()


def test_a_linked_preset_is_not_read(tmp_path, outside):
    from control_plane import scaffolds
    g = tmp_path / "global"
    _link(g / "asks" / "onboard.md", outside / "secret.md")
    try:
        _fm, body = scaffolds.read_preset(g, "onboard", image_root=None)
    except scaffolds.ScaffoldError:
        return
    assert SECRET not in body and SECRET not in json.dumps(_fm)


def test_proposals_neither_read_nor_written_through_a_linked_vexa(tmp_path, outside):
    from shared import proposals
    victim = outside / "proposals.json"
    victim.write_text(json.dumps({"items": [{"id": "x", "act": SECRET, "status": "open"}]}))
    desk = tmp_path / "desk"
    desk.mkdir()
    _link(desk / ".vexa", outside)
    assert SECRET not in json.dumps(proposals.read(desk))
    before = victim.read_text()
    with contextlib.suppress(OSError, ValueError):
        proposals.add(desk, source="s", act="a")
    assert victim.read_text() == before


def test_desk_readme_is_neither_read_nor_written_through_a_link(tmp_path, outside):
    from shared import desk_readme
    victim = outside / "README.md"
    victim.write_text(f"# {SECRET}\n")
    desk = tmp_path / "desk"
    desk.mkdir()
    _link(desk / "README.md", victim)
    desk_readme.update_readme(desk, home_id="w1", name="Ada")
    assert victim.read_text() == f"# {SECRET}\n"
    assert SECRET not in (desk / "README.md").read_text()


def test_desk_cards_and_now_do_not_read_a_linked_page(tmp_path, outside):
    from shared import desk_now, desk_readme
    desk = tmp_path / "desk"
    _link(desk / "kg" / "entities" / "meeting" / "m.md", outside / "secret.md")
    (outside / "person").mkdir()
    (outside / "person" / "p.md").write_text((outside / "secret.md").read_text())
    _link(desk / "kg" / "entities" / "person", outside / "person")
    mounts = [{"path": str(desk), "id": "w1", "home": True}]
    assert SECRET not in json.dumps([c.title for c in desk_readme.cards(mounts, "w1")])
    assert SECRET not in json.dumps(desk_now.dated_pages(desk), default=str)


# ── llm: Codex home, MCP attachment ──────────────────────────────────────────────────────────────

def test_codex_tools_home_is_not_written_through_a_planted_link(monkeypatch, tmp_path, outside):
    from llm import codex
    home = tmp_path / "codex"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"token": "CRED"}))
    _link(tmp_path / "codex-tools", outside)
    monkeypatch.setenv("CODEX_HOME", str(home))
    got = codex._tools_codex_home((os.getuid(), os.getgid()))
    assert not (outside / "auth.json").exists()
    assert not got.is_symlink()


def test_a_linked_codex_credential_is_not_copied(monkeypatch, tmp_path, outside):
    from llm import codex
    home = tmp_path / "codex"
    home.mkdir()
    (outside / "other.json").write_text(json.dumps({"token": SECRET}))
    _link(home / "auth.json", outside / "other.json")
    monkeypatch.setenv("CODEX_HOME", str(home))
    codex._tools_codex_home((os.getuid(), os.getgid()))
    copy = tmp_path / "codex-tools" / "auth.json"
    assert not copy.exists() or SECRET not in copy.read_text()


def _linked_mcp(tmp_path: Path, outside: Path) -> Path:
    (outside / "mcp.json").write_text(json.dumps({"mcpServers": {"vexa": {
        "type": "http", "url": "http://evil.invalid/mcp", "headers": {"Authorization": SECRET}}}}))
    base = tmp_path / "base"
    _link(base / ".claude" / "mcp.json", outside / "mcp.json")
    return base / ".claude" / "mcp.json"


def test_a_linked_mcp_attachment_is_not_read_by_any_harness(tmp_path, outside):
    from llm import codex, openai_agent
    from worker import engine
    cfg = _linked_mcp(tmp_path, outside)
    assert engine._mcp_endpoint(str(cfg)) is None
    assert SECRET not in json.dumps(codex._mcp_config(str(cfg), ["mcp__vexa"]))
    calls: list = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: calls.append(str(req.url)) or httpx.Response(500)))
    servers, index = openai_agent._load_mcp(str(cfg), http_client=client)
    assert servers == [] and index == {} and calls == []


def test_tool_grant_does_not_write_the_mcp_config_through_a_linked_claude(tmp_path, outside):
    from tests.test_tools import _registry
    from shared.tools import apply_tool_grant
    reg = _registry(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    _link(ws / ".claude", outside)
    with contextlib.suppress(OSError, ValueError):
        apply_tool_grant(ws, ["email"], reg)
    assert not (outside / "mcp.json").exists()


# ── llm: the OpenAI harness's tools and its transcript ───────────────────────────────────────────

def test_openai_read_acts_on_what_it_checked_not_on_a_link_swapped_in(monkeypatch, tmp_path, outside):
    """R5-1's race, in the harness: the path is checked (resolved inside the mount), then a directory
    on it is swapped for a link before the act. Opening by name would read through the swap."""
    from llm.openai_agent import _Sandbox, run_builtin
    (outside / "f.txt").write_text(SECRET)
    ws = (tmp_path / "ws").resolve()
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "f.txt").write_text("mine")
    real_resolve = Path.resolve
    swapped = {"done": False}

    def racing_resolve(self, *a, **kw):
        out = real_resolve(self, *a, **kw)
        if not swapped["done"] and str(self).endswith("sub/f.txt"):
            swapped["done"] = True
            os.rename(ws / "sub", ws / "sub-real")
            os.symlink(outside, ws / "sub")
        return out

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    ok, text = run_builtin("Read", {"file_path": str(ws / "sub" / "f.txt")}, _Sandbox([ws]))
    monkeypatch.setattr(Path, "resolve", real_resolve)
    assert swapped["done"] and SECRET not in text


def test_openai_write_acts_on_what_it_checked_not_on_a_link_swapped_in(monkeypatch, tmp_path, outside):
    from llm.openai_agent import _Sandbox, run_builtin
    ws = (tmp_path / "ws").resolve()
    (ws / "sub").mkdir(parents=True)
    real_resolve = Path.resolve
    swapped = {"done": False}

    def racing_resolve(self, *a, **kw):
        out = real_resolve(self, *a, **kw)
        if not swapped["done"] and str(self).endswith("sub/new.txt"):
            swapped["done"] = True
            os.rename(ws / "sub", ws / "sub-real")
            os.symlink(outside, ws / "sub")
        return out

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    run_builtin("Write", {"file_path": str(ws / "sub" / "new.txt"), "content": "x"}, _Sandbox([ws]))
    monkeypatch.setattr(Path, "resolve", real_resolve)
    assert swapped["done"] and not (outside / "new.txt").exists()


def test_openai_transcript_is_neither_appended_nor_replayed_through_a_link(tmp_path, outside):
    from llm.openai_agent import _Transcript
    work = (tmp_path / "work").resolve()
    work.mkdir()
    chat = tmp_path / "chat"
    chat.mkdir()
    t0 = _Transcript(chat, work, "sid")
    victim = outside / "victim.jsonl"
    victim.write_text(json.dumps({"oa": {"role": "user", "content": SECRET}}) + "\n")
    _link(t0.path, victim)
    t = _Transcript(chat, work, "sid")
    assert t.messages() == []                                # not replayed into the model
    t.append({"type": "user", "oa": {"role": "user", "content": "hi"}})
    assert victim.read_text().count("\n") == 1               # not appended through
    chat2 = tmp_path / "chat2"
    chat2.mkdir()
    (outside / "projects").mkdir()
    _link(chat2 / ".claude", outside)
    _Transcript(chat2, work, "sid").append({"type": "user"})
    assert list((outside / "projects").iterdir()) == []


# ── agent-api: grounding, links, steering, flow pages, the company layer, `_system` ──────────────

def test_workspace_grounding_does_not_fold_a_linked_readme(tmp_path, outside):
    from types import SimpleNamespace
    from control_plane.api_shared import _fold_workspace_grounding
    ws = tmp_path / "ws"
    ws.mkdir()
    _link(ws / "README.md", outside / "secret.md")
    mount = SimpleNamespace(slug="ws1", workspace_id="w1", name="WS", path=str(ws))
    assert SECRET not in _fold_workspace_grounding([mount], "ws1")


def test_meeting_steering_override_is_not_read_through_a_link(tmp_path, outside):
    from control_plane import meeting_steering
    (outside / "o.md").write_text(f"## live\n{SECRET}\n")
    g = tmp_path / "global"
    _link(g / "agents" / "meeting-lifecycle.md", outside / "o.md")
    assert SECRET not in json.dumps(meeting_steering.steering_templates(str(g)))


def test_flow_pages_are_not_written_through_a_linked_flows_dir(tmp_path, outside):
    from control_plane import flow_pages_watch as watch
    g = tmp_path / "global"
    g.mkdir()
    _link(g / "flows", outside)
    idx = lambda: [{"file": "f@1.md", "etag": "x"}]                       # noqa: E731
    bod = lambda files: [{"file": "f@1.md", "body": "page\n"}]           # noqa: E731
    assert watch.reconcile(g, index_fn=idx, bodies_fn=bod) == []
    assert not (outside / "f@1.md").exists()


def test_company_layer_state_does_not_read_a_linked_file(tmp_path, outside):
    from control_plane import global_layer
    g = tmp_path / "global"
    g.mkdir()
    (outside / "README.md").write_text(f"# {SECRET}\n\nCompany: {SECRET}\n")
    _link(g / "README.md", outside / "README.md")
    assert SECRET not in json.dumps(global_layer.state(g), default=str)


def test_system_workspace_first_write_does_not_follow_a_planted_link(tmp_path, outside):
    from control_plane import system_mounts
    root = tmp_path / "workspaces"
    home = root / ".system" / "u1"
    (outside / "identity.md").write_text("theirs\n")
    _link(home / "identity.md", outside / "identity.md")
    system_mounts.ensure_system_workspace(str(root), "u1")
    assert (outside / "identity.md").read_text() == "theirs\n"


def test_a_link_chip_does_not_take_its_title_through_a_link(tmp_path, outside):
    from control_plane import link_resolver, workspace_ids as ids
    from workspaces.shared import workspace_id as wid
    root = tmp_path / "workspaces"
    ws = root / "u1"
    _link(ws / "kg" / "entities" / "person" / "eve.md", outside / "secret.md")
    reg = ids.WorkspaceRegistry()
    gid = "abcdefghijklmnopqrstuvwxyz234567"[: wid.ID_LEN]
    reg.put({"id": gid, "slug": "u1", "dir": str(ws), "kind": "desk", "name": "Desk", "owner": "u1"})
    out = link_resolver.resolve(f"ws:{gid}/eve", subject="u1", root=root, registry=reg)
    assert SECRET not in json.dumps(out)


# ── worker and shared: first provision, seeds, entity write-back, governance ─────────────────────

def test_first_provision_does_not_write_memory_through_a_link(monkeypatch, tmp_path, outside):
    from worker import engine
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(tmp_path / "no-such-seed"))
    work = tmp_path / "work"
    (outside / "CLAUDE.md").write_text("theirs\n")
    _link(work / "CLAUDE.md", outside / "CLAUDE.md")
    with contextlib.suppress(Exception):
        engine._ensure_repo(work)
    assert (outside / "CLAUDE.md").read_text() == "theirs\n"


def test_seeding_does_not_copy_the_template_through_a_linked_folder(tmp_path, outside):
    from shared.seeding import seed_workspace
    seed = tmp_path / "seed"
    (seed / "kg" / "templates").mkdir(parents=True)
    (seed / "kg" / "templates" / "person.md").write_text("tpl\n")
    (seed / "CLAUDE.md").write_text("conv\n")
    ws = tmp_path / "ws"
    ws.mkdir()
    _link(ws / "kg", outside)
    before = _snapshot(outside)
    with contextlib.suppress(Exception):
        seed_workspace(ws, seed)
    assert _snapshot(outside) == before and not (outside / "entities").exists()


def test_global_seed_top_up_does_not_write_through_a_dangling_link(tmp_path, outside):
    from control_plane import global_seed
    src = tmp_path / "src"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "a.md").write_text("seed\n")
    g = tmp_path / "global"
    _link(g / "agents" / "a.md", outside / "planted.md")         # dangling
    global_seed.top_up(g, [(src, "")])
    assert not (outside / "planted.md").exists()


def test_link_back_write_does_not_follow_a_link_swapped_in(monkeypatch, tmp_path, outside):
    from workspaces.shared import entities
    root = tmp_path / "ws"
    page = root / "kg" / "entities" / "person" / "bob.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: person\nid: bob\ntitle: Bob\n---\n# Bob\n")
    victim = outside / "victim.md"
    victim.write_text("theirs\n")
    real_plan = entities.plan_link_back

    def plan_then_swap(*a, **kw):
        plan = real_plan(*a, **kw)
        if plan:
            (root / plan[0]).unlink()
            os.symlink(victim, root / plan[0])
        return plan

    monkeypatch.setattr(entities, "plan_link_back", plan_then_swap)
    entities.link_back(root, "Bob", "Acme", "works_at")
    assert victim.read_text() == "theirs\n"


def test_governance_reports_a_linked_entity_instead_of_reading_it(tmp_path, outside):
    from shared.governance import revalidate_entities
    work = tmp_path / "work"
    _link(work / "kg" / "entities" / "person" / "eve.md", outside / "secret.md")
    out = revalidate_entities(work, ["kg/entities/person/eve.md"])
    assert out and out[0][0] == "kg/entities/person/eve.md"


def test_onboarding_receipt_cannot_vouch_for_a_page_through_a_link(monkeypatch, tmp_path, outside):
    """The receipt names a graph page that must carry the source. Checked by resolving the path,
    then read by name: a `kg` swapped for a link after the check made root read the twin outside."""
    from control_plane.onboarding_research import Research, ResearchError

    def read(cid, args):
        if args["action"] == "gmail.search":
            return {"messages": [{"id": "first"}], "has_more": False, "next_page_token": ""}
        return {"message": {"id": args["message_id"], "body": "evidence"}}

    ws = (tmp_path / "u1").resolve()
    (ws / "kg").mkdir(parents=True)
    run = Research(ws, read)
    run.run("start", connections=[{"id": "a" * 32, "provider": "google_email"}])
    batch = run.run("next")
    sid = batch["items"][0]["source_id"]
    (ws / "kg" / "project.md").write_text("no source named here\n")
    twin = outside / "kg"
    twin.mkdir()
    (twin / "project.md").write_text(sid)
    receipt = [{"source_id": sid, "paths": ["kg/project.md"]}]
    real_resolve = Path.resolve
    swapped = {"done": False}

    def racing_resolve(self, *a, **kw):
        out = real_resolve(self, *a, **kw)
        if not swapped["done"] and str(self).endswith("/kg"):
            swapped["done"] = True
            os.rename(ws / "kg", ws / "kg-real")
            os.symlink(twin, ws / "kg")
        return out

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    with pytest.raises(ResearchError):
        run.run("ack", batch_id=batch["batch_id"], receipts=receipt)
    monkeypatch.setattr(Path, "resolve", real_resolve)
    if not swapped["done"]:
        os.rename(ws / "kg", ws / "kg-real")
        os.symlink(twin, ws / "kg")
    with pytest.raises(ResearchError):                       # planted outright: refused as well
        run.run("ack", batch_id=batch["batch_id"], receipts=receipt)


# ── the one rmtree ───────────────────────────────────────────────────────────────────────────────

def test_remove_tree_unlinks_a_link_and_never_empties_its_target(tmp_path, outside):
    from workspaces.shared import workspace_paths as wpaths
    (outside / "keep.txt").write_text("keep")
    store = tmp_path / "store"
    store.mkdir()
    _link(store / "slot", outside)
    wpaths.remove_tree(store / "slot")
    assert not os.path.lexists(store / "slot") and (outside / "keep.txt").exists()
    tree = store / "tree"
    (tree / "a").mkdir(parents=True)
    _link(tree / "a" / "out", outside)
    wpaths.remove_tree(tree)
    assert not tree.exists() and (outside / "keep.txt").exists()
    wpaths.remove_tree(store / "absent")                     # absent is a no-op
