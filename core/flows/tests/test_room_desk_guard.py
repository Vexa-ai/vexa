"""THE DECISION-22 GUARD REPAIRS ITSELF ONCE, AND OTHERWISE SAYS EXACTLY WHAT TO RUN.

Vexa-ai/vexa#1606. `process_meeting` records the organiser's desk HEAD before the post-meeting turn
and refuses the step if it moved — correctly: that run writes into no desk. The problem was never
the check, it was its EXIT. It was flatly terminal, so the recovery was a human resetting a
repository by hand and re-firing the reaction, and on 2026-09-06 that happened twice: meeting 147
(the entity write-back phase) and meeting 150 (three commits reading `175: README.md — updated`).
Both times the report existed, was grounded in the transcript, and went nowhere.

Decision 22 is enforced by the MOUNTS now (`control_plane.dispatch.build_mount_set` gives a room run
no writable desk of the subject's own — `core/agent/tests/test_post_meeting_run.py`), so this check
should never fire again. These rows are about what happens on the day it does:

  * it undoes the stray commits itself, once, and re-runs the turn — no human, no lost mail;
  * a second failure, or a reset that refuses, is terminal AND names the commits that landed and the
    one command that puts the desk back, so the manual recovery is a copy-paste;
  * a run whose desk did not move is untouched by any of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import flows_defs.production as production
import flows_steps.agent as ag
import pytest
from flows import Registry, StepError, Wait

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_link_loop import FakeScaffolds, _ctx, _StubDB  # noqa: E402

REPORT = "we agreed to defer the vote until next quarter"
TRANSCRIPT = "we agreed to defer the vote until next quarter, minuted"


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    monkeypatch.setattr(production, "mint_scaffold", FakeScaffolds())


def _rig(monkeypatch, *, head_after, reset=None):
    """`process_meeting` with everything but the desk-witness stubbed, and a RECORDER on the two
    acts this file is about: the reset, and the re-dispatch of the turn."""
    reg = Registry()
    production.build(reg, _StubDB())
    seen: dict = {"dispatched": [], "reset": []}
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: {"id": 412})
    monkeypatch.setattr(production.mt, "room_order", lambda uid, mid, p, n, cap=0: [])
    monkeypatch.setattr(production.mt, "transcript_text", lambda uid, mid: TRANSCRIPT)
    monkeypatch.setattr(production.ag, "collect_reply", lambda uid, s, base: REPORT)
    monkeypatch.setattr(production.ag, "head_sha", lambda uid: head_after)
    monkeypatch.setattr(production.ag, "head_subjects",
                        lambda uid, limit=3: ["a1b2c3d4e 175: README.md — updated",
                                              "9f8e7d6c5 175: README.md — updated"])

    def fake_dispatch(uid, session, prompt, room=None, **kw):
        seen["dispatched"].append(prompt)
        return 0

    def fake_reset(uid, sha, reason=""):
        seen["reset"].append((uid, sha, reason))
        return reset if reset is not None else {"reset": True, "before": head_after,
                                                "after": sha, "detail": ""}

    monkeypatch.setattr(production.ag, "dispatch_turn", fake_dispatch)
    monkeypatch.setattr(production.ag, "reset_desk", fake_reset)
    return reg, seen


def _run(reg, scratch):
    return reg.steps["process_meeting"](_ctx(
        {"uid": "175", "meeting_id": 150, "native": "abc", "organizer": "a@b.test",
         "title": "T", "start": 1_700_003_600.0}, scratch=scratch))


# ── the desk did not move: nothing here happens at all ───────────────────────────────────────
def test_a_run_that_wrote_no_desk_is_never_reset_and_never_re_dispatched(monkeypatch):
    reg, seen = _rig(monkeypatch, head_after="sha-before")
    out = _run(reg, {"baseline": 0, "row_id": 412, "head_before": "sha-before"})
    assert out.result["report"] == REPORT
    assert seen["reset"] == [] and seen["dispatched"] == []


# ── it moved: undo it, re-run the turn, tell the model what it did ───────────────────────────
def test_the_guard_resets_the_desk_to_the_witness_and_retries_itself_once(monkeypatch):
    """The two acts a human performed by hand on 2026-09-06, performed by the step that raised."""
    reg, seen = _rig(monkeypatch, head_after="sha-after")
    scratch = {"baseline": 0, "row_id": 412, "head_before": "sha-before"}
    out = _run(reg, scratch)

    assert isinstance(out, Wait), "a repaired run waits for the retry, it does not fail the meeting"
    assert seen["reset"] == [("175", "sha-before", "decision 22 · meeting 150")]
    assert len(seen["dispatched"]) == 1
    said = seen["dispatched"][0]
    assert "WROTE TO A DESK" in said
    assert "175: README.md — updated" in said, "the model is told what it actually committed"
    assert "removed" in said
    assert scratch["desk_reset"] is True, "the mark that makes this happen exactly once"


def test_a_second_failure_is_terminal_and_names_the_commits_and_the_one_command(monkeypatch):
    """The retry wrote again. That is a bug in something else and a human has to look — so the
    refusal carries everything they need to recover without reconstructing it: the shas, the commit
    subjects, and the reset call verbatim."""
    reg, seen = _rig(monkeypatch, head_after="sha-after")
    scratch = {"baseline": 0, "row_id": 412, "head_before": "sha-before", "desk_reset": True}
    with pytest.raises(StepError) as e:
        _run(reg, scratch)

    msg = str(e.value)
    assert "committed to the organiser's desk, and it must not (decision 22)" in msg
    assert "sha-befor -> sha-after" in msg
    assert "175: README.md — updated" in msg
    assert "/api/workspace/git/reset" in msg and '"sha":"sha-before"' in msg
    assert "X-User-Id: 175" in msg
    assert seen["reset"] == [], "it does not reset twice"
    assert e.value.retryable is False


def test_a_reset_it_could_not_perform_refuses_immediately_and_says_why(monkeypatch):
    """A probe that cannot repair must not pretend it did. The refusal keeps the ORIGINAL reason
    and appends the one the reset gave — losing either would leave a reader guessing."""
    reg, seen = _rig(monkeypatch, head_after="sha-after",
                     reset={"reset": False, "detail": "sha-before is not an ancestor of HEAD"})
    scratch = {"baseline": 0, "row_id": 412, "head_before": "sha-before"}
    with pytest.raises(StepError) as e:
        _run(reg, scratch)

    msg = str(e.value)
    assert "reset refused: sha-before is not an ancestor of HEAD" in msg
    assert "175: README.md — updated" in msg
    assert "/api/workspace/git/reset" in msg
    assert seen["dispatched"] == [], "a desk that is still dirty must not be handed another turn"


# ── the step under the step ──────────────────────────────────────────────────────────────────
def test_reset_desk_asks_the_internal_tier_for_this_subjects_own_desk(monkeypatch):
    """No slug on the wire, and the internal secret on it: agent-api takes no workspace name for
    this route, and refuses any caller that cannot present the header."""
    calls = []
    monkeypatch.setattr(ag, "require_internal_secret", lambda: "s3cr3t")
    monkeypatch.setattr(ag, "http", lambda m, u, h, b=None, **k: (
        calls.append((m, u, h, b)) or (200, {"reset": True, "before": "b", "after": "a"})))

    out = ag.reset_desk("175", "abc1234", reason="decision 22")
    method, url, headers, body = calls[0]
    assert method == "POST" and url.endswith("/api/workspace/git/reset")
    assert headers["X-User-Id"] == "175" and headers["X-Internal-Secret"] == "s3cr3t"
    assert body == {"sha": "abc1234", "reason": "decision 22"}
    assert "slug" not in body
    assert out["reset"] is True


def test_reset_desk_degrades_to_a_refusal_rather_than_raising(monkeypatch):
    """Its one caller is already inside a failure; a second exception on top of the first would
    replace the reason a human needs with the reason the repair did not work."""
    monkeypatch.setattr(ag, "require_internal_secret", lambda: "s3cr3t")
    monkeypatch.setattr(ag, "http", lambda *a, **k: (403, {"detail": "internal-tier capability"}))
    assert ag.reset_desk("175", "abc1234")["reset"] is False

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ag, "http", boom)
    out = ag.reset_desk("175", "abc1234")
    assert out["reset"] is False and "connection refused" in out["detail"]
