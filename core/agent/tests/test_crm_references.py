"""ADR-0047 d5: a workspace's links to CRM records are read from the COMMIT, published as the whole
set with the roster, once per head, and rebuildable from git."""
import json
import subprocess
from pathlib import Path

from control_plane import crm_references as cr

A = "0a1b2c3d-0000-4000-8000-00000000000a"
B = "0a1b2c3d-0000-4000-8000-00000000000b"


def _repo(root: Path, name: str, files: dict) -> Path:
    ws = root / name
    ws.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    for rel, text in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"], check=True)
    return ws


def test_links_and_front_matter_at_the_commit_with_the_roster(tmp_path):
    ws = _repo(tmp_path, "sales", {
        "accounts/globex.md": f"---\nentity: {A}\n---\nSee [Jane](/crm?record={B}) and [x](/crm?object=Deal).\n",
        "notes.md": "```\n[code](/crm?record=not-a-uuid)\n```\n",
        "policy/members.json": json.dumps([{"subject": "2", "role": "contributor"}]),
    })
    (ws / "draft.md").write_text(f"[uncommitted](/crm?record={A})")      # the work tree is not the fact
    snap = cr.snapshot(ws, "sales")
    assert snap["references"] == [{"page": "accounts/globex.md", "record": A},
                                  {"page": "accounts/globex.md", "record": B}]
    assert snap["members"] == [{"subject": "2", "role": "contributor"}]
    assert snap["commit"] and snap["committed_at"]


def test_one_fact_per_head_and_a_rebuild_republishes(tmp_path):
    _repo(tmp_path, "sales", {"a.md": f"[a](/crm?record={A})"})
    _repo(tmp_path, "empty", {"b.md": "nothing"})
    sent = []
    send = lambda event, sid, refs: sent.append((event, sid, refs)) or True  # noqa: E731
    seen = {}
    assert cr.reconcile(tmp_path, seen, send=send) == ["empty", "sales"]
    assert cr.reconcile(tmp_path, seen, send=send) == []                # head unchanged: nothing
    assert cr.reconcile(tmp_path, seen, force=True, send=send) == ["empty", "sales"]
    assert sent[0][0] == cr.EVENT and sent[0][1].startswith("refs-empty-")
    assert "members" not in sent[0][2]                                   # no roster file: absent


def test_off_unless_the_deployment_runs_the_crm(monkeypatch, tmp_path):
    monkeypatch.delenv(cr.POLL_ENV, raising=False)
    assert cr.start(tmp_path) is None


def test_a_desk_without_a_roster_is_owned_by_its_subject(tmp_path):
    ws = _repo(tmp_path, "126", {"notes.md": f"[a](/crm?record={A})"})
    snap = cr.snapshot(ws, "126")
    assert snap["members"] == [{"subject": "126", "role": "owner"}] and snap["uid"] == "126"
    shared = _repo(tmp_path, "sales", {"a.md": "x"})
    assert "members" not in cr.snapshot(shared, "sales")
