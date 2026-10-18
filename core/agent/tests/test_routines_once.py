"""ADR-0046 decision 5 — one mechanism: a `routines/*.md` file is converted ONCE and retired.

After the pass a routine is a flow and nothing else: the file is gone from the workspace (a copy
kept outside every mount), a file that cannot become a flow is SHOWN with the exact reason instead
of waiting on the old scheduler, a file an agent wrote becomes a paused flow (no separate
confirmation board), and a file whose intent a flow already has is merged into that one flow."""
from __future__ import annotations

import json
import os
import time

import pytest

from control_plane import routine_flows as rf
from tests.test_routine_flows import ROUTINE, FakeFlows, _as, world  # noqa: F401 — fixtures


class DatedFlows(FakeFlows):
    """FakeFlows with the `created_at` flows-api lists, so a merge can tell which side is newer."""

    def submit(self, subject, name, cron, instruction, *, activate=True):
        row = super().submit(subject, name, cron, instruction, activate=activate)
        self.rows[-1]["created_at"] = str(time.time())
        return dict(self.rows[-1])


@pytest.fixture
def flows(monkeypatch):
    fake = DatedFlows()
    monkeypatch.setattr(rf, "FlowsClient", lambda: fake)
    monkeypatch.setattr(rf, "configured", lambda: True)
    return fake


def _approve(root, name, subject="7"):
    """What the old mechanism recorded when a person stood behind a routine file's bytes."""
    import hashlib
    st = root / ".routines"
    (st / "approvals").mkdir(parents=True, exist_ok=True)
    (st / "approvals.v1.json").write_text("{}")
    rec = st / "approvals" / f"{subject}.json"
    data = json.loads(rec.read_text()) if rec.exists() else {}
    data[name] = hashlib.sha256((root / subject / "routines" / f"{name}.md").read_bytes()).hexdigest()
    rec.write_text(json.dumps(data))


def _retired(root, subject="7"):
    d = root / ".routines" / "retired" / subject
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def test_a_converted_file_is_retired_with_a_copy_and_never_converted_again(world, flows):
    client, scheduler, root = world
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert not (root / "7" / "routines" / "brief.md").exists(), "the file is retired"
    kept = _retired(root)
    assert len(kept) == 1 and kept[0].startswith("brief.")
    assert (root / ".routines" / "retired" / "7" / kept[0]).read_text() == ROUTINE
    before = len(flows.rows)
    for _ in range(3):
        rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert len(flows.rows) == before, "no pass converts it again"


def test_the_readme_in_routines_is_not_a_routine(world, flows):
    client, scheduler, root = world
    (root / "7" / "routines" / "README.md").write_text("# routines\n\nNothing here.\n")
    counts = rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert (root / "7" / "routines" / "README.md").exists()
    assert counts.files_skipped == 0
    cards = client.get("/api/routines", headers=_as()).json()["routines"]
    assert not any(c["name"] == "README" for c in cards)


def test_a_file_that_cannot_be_read_is_shown_with_the_reason(world, flows):
    client, scheduler, root = world
    (root / "7" / "routines" / "broken.md").write_text("---\nenabled: true\nprompt: do it\n")
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert not (root / "7" / "routines" / "broken.md").exists()
    assert any(n.startswith("broken.") for n in _retired(root))
    cards = client.get("/api/routines", headers=_as()).json()["routines"]
    card = next(c for c in cards if c["name"] == "broken")
    assert card["status"] == "refused" and card["enabled"] is False
    assert "cron" in card["refused_reason"]
    assert not any((j.get("metadata") or {}).get("name") == "broken" for j in scheduler.jobs)


def test_a_settings_block_left_open_is_still_read(world, flows):
    client, scheduler, root = world
    (root / "7" / "routines" / "open.md").write_text(
        "---\nenabled: true\ncron: \"0 7 * * *\"\nprompt: |\n  Look at the news.\n")
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    row = next(r for r in flows.rows if r["name"] == "open")
    assert row["trigger"]["cron"] == "0 7 * * *"
    assert row["params"]["instruction"].startswith("Look at the news.")


def test_a_routine_flows_refuses_is_shown_and_leaves_nothing_on_the_old_scheduler(world, flows):
    client, scheduler, root = world
    flows.refuse["brief"] = "quota_min_interval"
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert not any((j.get("metadata") or {}).get("source") == "workspace-routine"
                   for j in scheduler.jobs), "the job the file armed is cancelled"
    card = next(c for c in client.get("/api/routines", headers=_as()).json()["routines"]
                if c["name"] == "brief")
    assert card["status"] == "refused" and "quota_min_interval" in card["refused_reason"]
    # dismissing the card forgets the refusal; the copy of the file stays
    r = client.delete("/api/routines/file:brief", headers=_as())
    assert r.status_code == 200
    assert not any(c["name"] == "brief"
                   for c in client.get("/api/routines", headers=_as()).json()["routines"])
    assert _retired(root)


def test_a_file_an_agent_wrote_becomes_a_paused_flow_not_a_confirmation(world, flows):
    client, scheduler, root = world
    _approve(root, "brief")
    (root / "7" / "routines" / "sneaky.md").write_text(ROUTINE)     # nobody stood behind it
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    row = next(r for r in flows.rows if r["name"] == "sneaky")
    assert row["status"] == "draft", "converted, but it does not run until the person resumes it"
    cards = client.get("/api/routines", headers=_as()).json()["routines"]
    card = next(c for c in cards if c["name"] == "sneaky")
    assert card["enabled"] is False and "resume" in card["paused_reason"]
    assert not any(c.get("pending_confirmation") for c in cards)
    assert client.post("/api/routines/sneaky/confirm", headers=_as()).status_code == 410


def test_a_file_and_a_flow_with_the_same_intent_become_one_flow(world, flows):
    client, scheduler, root = world
    flows.submit("7", "check-email", "*/15 * * * *", "the flow's own instruction")
    flows.rows[-1]["created_at"] = str(time.time() - 3600)
    f = root / "7" / "routines" / "check-emails-notify-important-ones.md"
    f.write_text("---\nenabled: true\ncron: '*/30 * * * *'\nprompt: the file's instruction\n---\n")
    _approve(root, f.stem)
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    names = {r["name"] for r in flows.rows}
    assert "check-emails-notify-important-ones" not in names, "no second flow"
    newest = max((r for r in flows.rows if r["name"] == "check-email"), key=lambda r: r["version"])
    assert newest["trigger"]["cron"] == "*/30 * * * *", "the newer schedule is carried"
    assert newest["params"]["instruction"] == "the flow's own instruction", "the flow is kept"
    marker = json.loads((root / ".routines" / "migrated" / "7.json").read_text())
    entry = marker["files"]["check-emails-notify-important-ones"]
    assert entry["outcome"] == "merged" and entry["flow"] == "check-email"
    assert not f.exists()


def test_an_older_file_merges_without_moving_the_schedule(world, flows):
    client, scheduler, root = world
    f = root / "7" / "routines" / "check-email-old.md"
    f.write_text("---\nenabled: true\ncron: '0 * * * *'\nprompt: old\n---\n")
    _approve(root, f.stem)
    old = time.time() - 7200
    os.utime(f, (old, old))
    flows.submit("7", "check-email", "*/15 * * * *", "keep me")
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    rows = [r for r in flows.rows if r["name"] == "check-email"]
    assert len(rows) == 1 and rows[0]["trigger"]["cron"] == "*/15 * * * *"


def test_two_files_with_one_intent_become_one_flow(world, flows):
    client, scheduler, root = world
    for n, cron in (("weather", "0 6 * * *"), ("weather-today", "0 7 * * *")):
        (root / "7" / "routines" / f"{n}.md").write_text(
            f"---\nenabled: true\ncron: '{cron}'\nprompt: weather\n---\n")
        _approve(root, n)
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert len({r["name"] for r in flows.rows if r["name"].startswith("weather")}) == 1


def test_different_intents_stay_apart():
    assert rf.same_intent("check-email", "check-emails-mn-notify-important-ones")
    assert rf.same_intent("weather", "weather-today")
    assert not rf.same_intent("check-email", "check-calendar")
    assert not rf.same_intent("weather-lisbon", "weather-porto")
    assert not rf.same_intent("brief", "inbox")


def test_a_file_an_earlier_version_refused_is_shown_with_the_exact_reason(world, flows):
    client, scheduler, root = world
    import hashlib
    f = root / "7" / "routines" / "fast.md"
    f.write_text("---\nenabled: true\ncron: '* * * * *'\nprompt: ping\n---\n")
    (root / ".routines" / "migrated").mkdir(parents=True, exist_ok=True)
    (root / ".routines" / "migrated" / "7.json").write_text(json.dumps(
        {"!fast": hashlib.sha256(f.read_bytes()).hexdigest()}))
    flows.refuse["fast"] = "quota_min_interval"
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    card = next(c for c in client.get("/api/routines", headers=_as()).json()["routines"]
                if c["name"] == "fast")
    assert "quota_min_interval" in card["refused_reason"] and not f.exists()


def test_writing_a_routine_shaped_page_is_refused_with_the_flows_tool(world, flows):
    client, _, _ = world
    for path in ("routines/sweep.md", "notes/unanswered-inbox-sweep.md"):
        r = client.put("/api/workspace/file", headers=_as(), json={
            "path": path, "content": "---\nenabled: true\ncron: '*/15 * * * *'\nprompt: sweep\n---\n"})
        assert r.status_code == 422 and r.json()["detail"]["refused"] == "routine_files_retired"
        assert "flows_submit" in r.json()["detail"]["note"]
    ok = client.put("/api/workspace/file", headers=_as(), json={
        "path": "notes/plain.md", "content": "---\ntitle: x\n---\nA page that mentions cron: daily."})
    assert ok.status_code == 200


def test_the_routine_shape_is_read_narrowly():
    assert rf.looks_like_routine_file("a.md", "---\ncron: '0 9 * * *'\nprompt: x\n---\n")
    assert rf.looks_like_routine_file("a.md", "---\nenabled: true\ncron: '0 9 * * *'\n")
    assert not rf.looks_like_routine_file("a.md", "---\ncron: '0 9 * * *'\n---\n")
    assert not rf.looks_like_routine_file("a.txt", "---\ncron: x\nprompt: y\n---\n")
    assert not rf.looks_like_routine_file("a.md", "no settings block, cron: x prompt: y")


def test_a_routine_file_in_a_shared_workspace_becomes_its_owners_flow(world, flows):
    client, scheduler, root = world
    team = root / "team-abc123"
    (team / "policy").mkdir(parents=True)
    (team / "policy" / "members.json").write_text(json.dumps([{"subject": "7", "role": "owner"}]))
    (team / "routines").mkdir()
    (team / "routines" / "sweep.md").write_text(ROUTINE)
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert {r["scope"] for r in flows.rows if r["name"] == "sweep"} == {"user:7"}
    assert not any(r["scope"] == "user:team-abc123" for r in flows.rows)
    assert not (team / "routines" / "sweep.md").exists()
    assert any(n.startswith("sweep.") for n in _retired(root, "team-abc123"))
