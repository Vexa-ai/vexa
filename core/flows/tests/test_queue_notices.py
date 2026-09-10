"""STANDING NOTICES — the queue items a person's agent should read without asking for them.

An ordinary waiting item is read when an agent calls `whats_waiting`. Some things stay true BETWEEN
calls, and an agent that never asks never learns them — so a say-file may flag itself, and the
flagged ones are served on their own, small, cheap route that a caller can ask on every call.

The contract this file pins, in the order it matters:

  N1  the flag is BEHAVIOR'S — `notice: true` in a say-file's front-matter, an admin's edit, no deploy
  N2  a file with no front-matter declares nothing and behaves exactly as it always did
  N3  a malformed flag costs the flag, never the sentence
  N4  `/queue/waiting` carries `notice` on every item; `/queue/notices` carries the flagged say texts
  N5  the notices route is the same door as the waiting route — a person reads their own, or nobody's

Offline like the rest of the suite, and reusing `test_queue_waiting.py`'s composition exactly: real
sqlite rows written the way the engine writes them, and the real app through `TestClient`.

Fixture-local words only. No copy in this file is any deployment's copy.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_queue  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

T0 = 1_788_000_000.0

MINE = {"uid": "126", "meeting_id": "104", "organizer": "dima@vexa.ai"}
THEIRS = {"uid": "999", "meeting_id": "7", "organizer": "someone@else.test"}

STANDING = "A fixture sentence that stays true between calls."
ORDINARY = "A fixture sentence about something that just happened."


def _identity(subject):
    return ("126", "dima@vexa.ai") if str(subject) in ("126", "dima@vexa.ai") else ("999", "")


def _row(db, rid, refs, *, flow="post_meeting", step="process_meeting", status_="retrying",
         reason=None, at=T0):
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        reason, created_at, updated_at)
                  VALUES (:rid,:sid,'meeting.completed',:refs,:flow,4,:step,:st,0,0,:why,:c,:c)""",
               {"rid": rid, "sid": f"{rid}::{flow}", "refs": json.dumps(refs), "flow": flow,
                "step": step, "st": status_, "why": reason, "c": at})


def _copy(**by_flow):
    """A behavior tree, injected. `waiting`/`notices` take the reader as a parameter, which is the
    seam that keeps every test in this file independent of what any real tree happens to say."""
    def read(flow, reason_type):
        return by_flow.get(flow, flows_queue.Say(""))
    return read


# ── N1/N2/N3 · the front-matter, read off a say-file ──────────────────────────────────────────

def test_a_file_with_no_front_matter_is_its_own_text_and_declares_nothing():
    """N2, and it is the compatibility row: every say-file written before this existed."""
    got = flows_queue.parse("Just the words.\n\nOn two paragraphs.")
    assert got == "Just the words.\n\nOn two paragraphs."
    assert got.notice is False


def test_front_matter_declares_the_flag_and_is_not_part_of_the_words():
    got = flows_queue.parse(f"---\nnotice: true\n---\n{STANDING}")
    assert got.notice is True
    assert got == STANDING, "the fence must not reach the person"


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1", " true "])
def test_the_words_that_mean_yes(value):
    assert flows_queue.parse(f"---\nnotice: {value}\n---\n{STANDING}").notice is True


@pytest.mark.parametrize("value", ["false", "no", "0", "", "maybe", "later"])
def test_anything_else_means_no(value):
    """A flag nobody can read is OFF, never guessed on: a notice reaches an agent that did not ask
    for it, so the failure direction has to be silence."""
    assert flows_queue.parse(f"---\nnotice: {value}\n---\n{STANDING}").notice is False


def test_a_fence_that_never_closes_costs_the_flag_and_not_the_sentence():
    """N3. The failure of a flag must never be the failure of a sentence a person was owed."""
    got = flows_queue.parse(f"---\nnotice: true\n{STANDING}")
    assert got.notice is False
    assert STANDING in got


def test_a_say_is_a_string_everywhere_a_string_was_expected():
    """The compatibility claim, stated: every existing caller of `say()` keeps working because the
    thing it gets back still IS the text."""
    got = flows_queue.parse(f"---\nnotice: true\n---\n{STANDING}")
    assert isinstance(got, str)
    assert json.loads(json.dumps({"say": got}))["say"] == STANDING


def test_an_empty_body_under_a_flag_is_still_silence():
    """Silence is the filter (`behavior/queue/README.md`), and a flag does not override it: a file
    with a fence and no words says nothing, and a notice nobody wrote is not a notice."""
    assert flows_queue.parse("---\nnotice: true\n---\n") == ""


def test_a_private_behavior_tree_flags_its_own_file_with_no_deploy(monkeypatch, tmp_path):
    """THE SEAM A PRIVATE PACK ACTUALLY USES (`test_flow_packs.py` seam 2): a deployment mounts its
    own `$VEXA_BEHAVIOR_DIR/queue/`, and one line at the top of one file there is the whole of
    making an item standing. No code in this repo, and nothing rebuilt."""
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "post_meeting.pending.md").write_text(f"---\nnotice: yes\n---\n{STANDING}",
                                                   encoding="utf-8")
    monkeypatch.setenv("VEXA_BEHAVIOR_DIR", str(tmp_path))

    got = flows_queue.say("post_meeting", "pending")
    assert got == STANDING and got.notice is True

    db = SqliteDB()
    _row(db, "r-mine", MINE)
    assert flows_queue.notices(db, subject="126", now=T0,
                              identity=_identity)["notices"] == [STANDING]


def test_the_published_showcase_flags_nothing(monkeypatch):
    """This repo ships the mechanism and uses it for nothing — a notice reaches an agent that did
    not ask, so the OSS default is that there are none."""
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR", raising=False)
    behavior = Path(__file__).resolve().parents[3] / "behavior" / "queue"
    flagged = [f.name for f in sorted(behavior.glob("*.md"))
               if f.name != "README.md" and flows_queue.parse(f.read_text(encoding="utf-8")).notice]
    assert flagged == []


# ── N4 · the two projections ──────────────────────────────────────────────────────────────────

def test_waiting_carries_the_flag_on_the_item():
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=flows_queue.Say(STANDING, notice=True)))
    assert out["items"][0]["notice"] is True
    assert out["items"][0]["say"] == STANDING


def test_an_unflagged_item_says_so_rather_than_omitting_the_field():
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=flows_queue.Say(ORDINARY)))
    assert out["items"][0]["notice"] is False


def test_a_plain_string_from_an_injected_reader_is_an_unflagged_item():
    """The `copy` seam predates front-matter and callers hand it plain strings. Normalising rather
    than requiring a `Say` is what keeps this change additive."""
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=ORDINARY))
    assert out["items"][0]["notice"] is False


def test_notices_returns_the_flagged_say_texts_and_nothing_else():
    """N4. The whole answer is sentences: no reaction id, no flow name, no step, no typed reason.
    It rides along with unrelated work, so it carries the least it can."""
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    _row(db, "r-live", MINE, flow="live_meeting", status_="running")
    out = flows_queue.notices(
        db, subject="126", now=T0, identity=_identity,
        copy=_copy(post_meeting=flows_queue.Say(STANDING, notice=True),
                   live_meeting=flows_queue.Say(ORDINARY)))
    assert out["notices"] == [STANDING]
    assert "r-mine" not in json.dumps(out) and "post_meeting" not in json.dumps(out)


def test_nothing_flagged_is_an_empty_list_not_a_summary_of_the_queue():
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.notices(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=flows_queue.Say(ORDINARY)))
    assert out["notices"] == []


def test_two_reactions_of_one_flow_say_it_once():
    """One thing that is true twice reads as two things."""
    db = SqliteDB()
    _row(db, "r-a", MINE)
    _row(db, "r-b", MINE, step="another_step")
    out = flows_queue.notices(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=flows_queue.Say(STANDING, notice=True)))
    assert out["notices"] == [STANDING]


def test_notices_are_scoped_to_the_subject_like_every_other_read():
    db = SqliteDB()
    _row(db, "r-theirs", THEIRS, status_="failed", reason="something on their side")
    out = flows_queue.notices(db, subject="126", now=T0, identity=_identity,
                              copy=_copy(post_meeting=flows_queue.Say(STANDING, notice=True)))
    assert out["notices"] == []


def test_an_unresolvable_subject_fails_closed():
    """Same direction as `pending`/`waiting`: an unresolvable subject never falls through to an
    unscoped read, which is how a scoping bug becomes the leak the scope was added to close."""
    out = flows_queue.notices(SqliteDB(), subject="nobody", now=T0,
                              identity=lambda s: (None, None))
    assert out["unresolved"] is True and out["notices"] == []


# ── N5 · the route — same door as `/queue/waiting` ────────────────────────────────────────────

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://queue-notices:unreachable@127.0.0.1:1/flows"}


@pytest.fixture(scope="module")
def api():
    """`test_queue_waiting.py::api`'s composition, verbatim: the real app against an unreachable
    Postgres, then handed a working `SqliteDB`."""
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    flows_api.db = SqliteDB()
    return flows_api, TestClient(flows_api.app)


def _seed(flows_api, monkeypatch, *, flagged: bool):
    flows_api.db.execute("DELETE FROM reaction")
    _row(flows_api.db, "r-mine", MINE)
    _row(flows_api.db, "r-theirs", THEIRS)
    monkeypatch.setattr(flows_queue, "resolve_identity", _identity)
    monkeypatch.setattr(flows_queue, "say",
                        _copy(post_meeting=flows_queue.Say(STANDING, notice=flagged)))


def _operator(**extra):
    return {"X-Flows-Admin-Key": _ENV["VEXA_FLOWS_API_KEY"], **extra}


def test_the_route_answers_with_this_person_s_notices(api, monkeypatch):
    flows_api, client = api
    _seed(flows_api, monkeypatch, flagged=True)
    r = client.get("/queue/notices?subject=126", headers=_operator())
    assert r.status_code == 200
    assert r.json()["notices"] == [STANDING]


def test_an_unflagged_queue_answers_with_no_notices(api, monkeypatch):
    flows_api, client = api
    _seed(flows_api, monkeypatch, flagged=False)
    assert client.get("/queue/notices?subject=126", headers=_operator()).json()["notices"] == []


def test_the_stamped_identity_wins_over_a_query_argument(api, monkeypatch):
    """The security row, and it is the same one `/queue/waiting` carries: a caller who names
    somebody else reads their OWN notices."""
    flows_api, client = api
    _seed(flows_api, monkeypatch, flagged=True)
    r = client.get("/queue/notices?subject=999", headers=_operator(**{"X-User-Id": "126"}))
    assert r.json()["subject"] == "126"


def test_no_subject_at_all_is_refused_not_answered_with_the_instance(api):
    _flows_api, client = api
    assert client.get("/queue/notices", headers=_operator()).status_code == 400


def test_the_route_is_behind_a_credential(api):
    _flows_api, client = api
    assert client.get("/queue/notices?subject=126").status_code == 401


def test_the_route_is_not_a_tool(api):
    """The mechanism adds a ROUTE, never a verb: an agent does not call this, the edge does. The
    manifest is what decides the tool list, and it does not name this route."""
    manifest = json.loads((Path(__file__).resolve().parents[1] / "mcp.tools.v1.json")
                          .read_text(encoding="utf-8"))
    paths = {t.get("route", {}).get("path") for t in manifest["tools"]}
    assert "/queue/notices" not in paths
