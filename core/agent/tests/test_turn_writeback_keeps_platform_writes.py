"""The turn's write-back must never delete what the turn did not touch (Vexa-ai/vexa#1645).

Founder, 2026-09-07, opening an invite the agent had minted a minute earlier: **"This invite link is
not valid. Ask whoever sent it for a new one."** The workspace's git log said it once per mint::

    8dfff9b 19:28:08  policy: mint invite 41cdb3b6a5841ffc (contributor) for oenb-b5e60c
    1a452f9 19:28:09  oenb-b5e60c: policy/invites.json — removed

The mint is agent-api writing its own store DURING the turn. The removal one second later is the
turn's write-back: it captured HEAD before the turn, rebuilt the whole `policy/` subtree from that
sha afterwards, and committed the deletion. One write surface, two writers, and the second one won
silently — which is the only way that failure ever presents.

Every test here drives the real `run_harness_turn` over a real git workspace, with a fake harness
standing in for the model. The "agent-api" writes are the real `workspace_membership` calls, made at
the same moment they are made in production: while the turn is running.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane import workspace_membership as m
from llm.ports import _commit_mount, run_harness_turn

WS = "pilot"
OWNER = "u_owner"
INVITEE = "jsmith@example.com"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, workspace_id: str = WS) -> Path:
    ws = root / workspace_id
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _owned(root: Path) -> Path:
    ws = _init_ws(root)
    m.ensure_owner(root, WS, OWNER, index=m.InMemoryMembershipIndex(), email="owner@example.test",
                   commit_fn=m.policy_commit)
    return ws


class _Harness:
    """A turn that does whatever ``during`` does, then writes a note like any real turn."""

    def __init__(self, during=lambda: None):
        self._during = during

    def run_turn(self, work, prompt, *, allowed_tools, session, model, mcp_config=None):
        self._during()
        (Path(work) / "note.md").write_text("the turn's own work\n")
        yield {"type": "done", "reply": "did work", "sessionId": "s1", "ok": True}


# ── the founder's report, as a test ───────────────────────────────────────────────────────────────
def test_an_invite_minted_mid_turn_is_still_redeemable_after_the_turn(tmp_path):
    """The whole of it: mint while the turn runs, let the turn write back, then open the link."""
    ws = _owned(tmp_path)
    minted: list = []

    def mint():
        minted.append(m.mint_invite(tmp_path, WS, role="contributor", created_by=OWNER,
                                    mode="restricted", allowed_emails=[INVITEE],
                                    commit_fn=m.policy_commit))

    list(run_harness_turn(ws, "go", _Harness(mint)))

    token = minted[0].token
    preview = m.preview_invite(tmp_path, token)
    assert preview is not None, "the turn's write-back deleted the invite it never had"
    assert preview["workspace_id"] == WS and preview["valid"] is True
    accepted = m.accept_invite(tmp_path, WS, token=token, subject="u_jsmith",
                               subject_email=INVITEE, index=m.InMemoryMembershipIndex(),
                               commit_fn=m.policy_commit)
    assert accepted["role"] == "contributor"


# ── the general rule the invite was one instance of ───────────────────────────────────────────────
def test_a_policy_file_written_mid_turn_by_the_platform_survives_the_write_back(tmp_path):
    """agent-api creates a file under `policy/` WHILE the turn is running — a membership granted
    from the panel or a redeemed invite. The write-back must commit around it, not over it."""
    ws = _init_ws(tmp_path)          # no policy/ at all when the turn starts
    idx = m.InMemoryMembershipIndex()

    def grant():
        m.ensure_owner(tmp_path, WS, OWNER, index=idx, email="owner@example.test",
                       commit_fn=m.policy_commit)

    events = list(run_harness_turn(ws, "go", _Harness(grant)))

    assert m.is_member(tmp_path, WS, OWNER) == "owner", "the platform's mid-turn write was deleted"
    assert (ws / m.MEMBERS_FILE).exists()
    assert "policy/members.json" in _git(ws, "ls-tree", "-r", "--name-only", "HEAD")
    assert [e for e in events if e["type"] == "commit"], "the turn's own work must still commit"
    assert (ws / "note.md").exists()


def test_a_membership_granted_mid_turn_survives_the_write_back(tmp_path):
    """The same, on a workspace that already had a roster: the new row must not be rolled back to
    the pre-turn one."""
    ws = _owned(tmp_path)
    idx = m.InMemoryMembershipIndex()

    def grant():
        m.grant_membership(tmp_path, WS, "u_jsmith", "contributor", added_by=OWNER, index=idx,
                           email=INVITEE, commit_fn=m.policy_commit)

    list(run_harness_turn(ws, "go", _Harness(grant)))

    assert m.is_member(tmp_path, WS, "u_jsmith") == "contributor"
    assert "u_jsmith" in _git(ws, "show", "HEAD:policy/members.json")


def test_an_ordinary_file_written_mid_turn_by_another_writer_survives(tmp_path):
    """Nothing about this is specific to `policy/`. A file agent-api drops into the workspace while
    the turn runs — a scaffold, an identity stamp, a published artefact — is committed, never
    removed, because the write-back records the tree it FINDS rather than a remembered one."""
    ws = _owned(tmp_path)

    def write():
        (ws / "arrived-mid-turn.md").write_text("written by agent-api\n")

    list(run_harness_turn(ws, "go", _Harness(write)))

    assert (ws / "arrived-mid-turn.md").exists()
    assert "arrived-mid-turn.md" in _git(ws, "ls-tree", "-r", "--name-only", "HEAD")


def test_a_quiet_turn_writes_nothing_under_policy(tmp_path):
    """The guard used to purge and re-checkout `policy/` on EVERY turn, including the overwhelming
    majority that never went near it — which is what made a race with the platform's writer
    inevitable. A turn that does not touch `policy/` must now leave it completely alone."""
    ws = _owned(tmp_path)
    before = (ws / m.MEMBERS_FILE).read_text()

    events = list(run_harness_turn(ws, "go", _Harness()))

    assert [e for e in events if e["type"] == "policy-reverted"] == []
    assert (ws / m.MEMBERS_FILE).read_text() == before


def test_an_attached_workspaces_carried_member_list_is_not_wiped_by_a_turn(tmp_path):
    """THE SECOND INSTANCE, found working the first. An ATTACHED group — one whose tree is somebody's
    cloned repo — keeps `policy/` untracked and locally excluded on purpose
    (`workspace_attach.carry_policy`: our subject ids must not be pushed to their repository, and one
    local commit would diverge the fresh clone). To a baseline-diffing guard that file looked exactly
    like an agent's untracked add, so every turn deleted it — an access wipe of the whole group, which
    is the one thing `carry_policy` exists to prevent."""
    ws = _init_ws(tmp_path)
    m.ensure_owner(tmp_path, WS, OWNER, index=m.InMemoryMembershipIndex(),
                   commit_fn=None)                     # written to disk, deliberately NOT committed
    (ws / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (ws / ".git" / "info" / "exclude").write_text("/policy/\n")
    assert m.is_member(tmp_path, WS, OWNER) == "owner"

    events = list(run_harness_turn(ws, "go", _Harness()))

    assert m.is_member(tmp_path, WS, OWNER) == "owner", "the group lost every member to a turn"
    assert [e for e in events if e["type"] == "policy-reverted"] == []
    # and it stayed OUT of git, which is the other half of what carry_policy promised
    assert "policy/members.json" not in _git(ws, "ls-tree", "-r", "--name-only", "HEAD")


# ── and the guard still guards ────────────────────────────────────────────────────────────────────
def test_the_agents_own_policy_write_is_still_reverted(tmp_path):
    """The property the guard exists for, unchanged: an agent-authored `policy/` write — here made
    mid-turn alongside a legitimate platform write in the SAME turn — never survives, while the
    platform's does. Both halves in one turn, because either alone would let the bug back."""
    ws = _owned(tmp_path)
    idx = m.InMemoryMembershipIndex()
    original = (ws / m.MEMBERS_FILE).read_text()

    def both():
        # the platform, legitimately, mid-turn
        m.mint_invite(tmp_path, WS, role="viewer", created_by=OWNER, commit_fn=m.policy_commit)
        m.grant_membership(tmp_path, WS, "u_real", "viewer", added_by=OWNER, index=idx,
                           commit_fn=m.policy_commit)
        # the agent, forging a roster of its own
        (ws / m.MEMBERS_FILE).write_text('[{"subject":"attacker","role":"owner"}]\n')
        (ws / "policy" / "evil.json").write_text("{}\n")

    events = list(run_harness_turn(ws, "go", _Harness(both)))

    reverted = [p for e in events if e["type"] == "policy-reverted" for p in e["paths"]]
    assert m.MEMBERS_FILE in reverted and "policy/evil.json" in reverted
    assert not (ws / "policy" / "evil.json").exists()
    assert m.is_member(tmp_path, WS, "attacker") is None
    # the PLATFORM's mid-turn write survives the same guard that discarded the agent's
    assert m.is_member(tmp_path, WS, "u_real") == "viewer"
    assert original != (ws / m.MEMBERS_FILE).read_text()  # it is the platform's roster, not the seed
    assert "attacker" not in _git(ws, "show", "HEAD:policy/members.json")


# ── the commit itself refuses to record a deletion it did not make ────────────────────────────────
def test_the_turn_commit_never_records_a_policy_deletion_it_did_not_make(tmp_path):
    """Belt to the guard's braces, and the line that would have stopped `policy/invites.json —
    removed` on its own. `git add -A` stages the tree AS IT FINDS IT, so a `policy/` path missing at
    that instant — for any reason, including a writer that is mid-write — is otherwise committed as
    a deletion. Only the guard may remove a `policy/` path, and it says which."""
    ws = _owned(tmp_path)
    (ws / "note.md").write_text("the turn's work\n")
    (ws / m.MEMBERS_FILE).unlink()          # gone, and NOT by the guard's hand

    sha = _commit_mount(ws, message="agent turn", author=("68", "68@vexa.local"))

    assert sha, "the turn's own work must still commit"
    assert "policy/members.json" in _git(ws, "ls-tree", "-r", "--name-only", "HEAD")
    assert (ws / m.MEMBERS_FILE).exists(), "and it is put back on disk, not just in the index"
    assert json.loads((ws / m.MEMBERS_FILE).read_text())[0]["subject"] == OWNER
    assert "note.md" in _git(ws, "show", "--name-only", "--format=", "HEAD")
