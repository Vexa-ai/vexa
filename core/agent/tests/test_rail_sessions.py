"""THE RAIL IS THE SERVER'S, NOT ONE BROWSER'S (Vexa-ai/vexa#1591).

Founder walk, 2026-09-06: after a morning of work on this instance — the global scaffold, a meeting
chat, several Extend jobs — he signed in again in a new window and got an empty rail and a chat that
introduced the product to him. *"i logged in again and now see no chats and it's starting over again
while it has the context"*.

Two halves, both proven here (the client's derivation and merge are pinned in
`clients/terminal/src/minutes/__tests__/railFromSessions.test.ts`):

  1. **the session index carries what a rail row shows** — the mount set, the record the chat was
     composed from, and whether a PERSON has written in it. Old rows keep meaning what they meant:
     the four original field names are untouched and an absent `touched` reads as yes.
  2. **`/internal/has-history` answers "is there anything to come back to"** — the question the
     terminal's `arrival()` asks before it mints a `first-visit`, which it used to mint on every
     sign-in that named no destination.

Plus the one thing about that arrival that turned out to need no change and therefore needs a test:
the opening rule #1583 put at the HEAD of the composed opening already rides on EVERY kind, so
`first-visit` has it. Pinned so it cannot quietly become setup-only again.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import scaffolds as scaffolds_mod
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

INTERNAL = "internal-tier-secret-for-tests"

FIRST_VISIT = """---
label: welcome
mounts: _global, personal
---
[first-visit] They signed in with no link. Their state is `{{state}}`.
"""


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None):
        yield {"type": "turn-complete"}


class _FakeRedis:
    """The three primitives `_Sessions` uses, and nothing else."""

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.sets: dict[str, set] = {}

    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.hashes.pop(key, None)


def _index_cases():
    """Both backings, every time. The in-memory fallback is what the unit tests run on and the redis
    hash is what production runs on, and a field that lands in one and not the other is a rail that
    works on a developer's laptop only."""
    return [_Sessions(), _Sessions(_FakeRedis())]


# ── 1. the index carries the rail's fields ───────────────────────────────────────────────────────

def test_session_carries_mounts_and_the_record_it_was_composed_from():
    for sess in _index_cases():
        sess.upsert("u1", "scaffold-SC1", title="Welcome",
                    workspaces=["_global", "u1", "grp-showb"],
                    scaffold={"kind": "first-visit", "id": "SC1"})
        row = sess.list("u1")[0]
        assert row["workspaces"] == ["_global", "u1", "grp-showb"]
        assert row["scaffold"] == {"kind": "first-visit", "id": "SC1"}
        # the four original names are exactly what they were — every existing consumer still reads
        assert row["session"] == "scaffold-SC1" and row["title"] == "Welcome"
        assert row["created"] > 0 and row["last_active"] > 0


def test_a_half_scaffold_record_is_dropped_not_repaired():
    """F37, one level down: a kind with no record id is the shape that let a planted row render the
    pre-scaffold admin card. It must not be constructible on the wire either."""
    for sess in _index_cases():
        sess.upsert("u1", "s", scaffold={"kind": "admin-setup"})
        assert sess.list("u1")[0]["scaffold"] is None


def test_touched_latches_and_a_machinery_only_thread_says_so():
    for sess in _index_cases():
        sess.upsert("u1", "opening", title="Welcome", touched=False)   # a scaffold's opening
        assert sess.list("u1")[0]["touched"] is False
        sess.upsert("u1", "opening", touched=True)                      # they answered
        assert sess.list("u1")[0]["touched"] is True
        sess.upsert("u1", "opening", touched=False)                     # …and machinery after that
        assert sess.list("u1")[0]["touched"] is True                    # never un-writes it


def test_a_row_older_than_the_field_is_a_conversation_that_happened():
    """Absent `touched` → yes. The defect being fixed is chats that do not show, so the fallback
    goes towards showing; a row written before this field existed had a turn in it either way."""
    r = _FakeRedis()
    r.hashes["agent:session:u1:legacy"] = {"created": "1.0", "last_active": "2.0", "title": "old"}
    r.sets["agent:sessions:u1"] = {"legacy"}
    row = _Sessions(r).list("u1")[0]
    assert row["touched"] is True and row["workspaces"] == [] and row["scaffold"] is None


def test_restating_mounts_does_not_need_the_title_again():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="First prompt", workspaces=["personal"])
        sess.upsert("u1", "s", workspaces=["personal", "grp-showb"])
        row = sess.list("u1")[0]
        assert row["title"] == "First prompt"
        assert row["workspaces"] == ["personal", "grp-showb"]


# ── 2. the HTTP surface ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    (root / "_global" / "asks" / "first-visit.md").write_text(FIRST_VISIT)
    return {"root": root, "subjects": {"priya@acme.test": "u_priya", "leo@acme.test": "u_leo"},
            "sessions": _Sessions()}


@pytest.fixture
def client(stack):
    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL,
        ui_url="https://app.example.test",
        redis_url="",
    )
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_FakeReader(),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"],
                     email_subject_lookup=lambda a: stack["subjects"].get(str(a).lower()))
    return TestClient(app)


def test_sessions_list_hands_the_rail_everything_a_row_shows(client, stack):
    stack["sessions"].upsert("u_priya", "meet-42", title="DNA TSC",
                             workspaces=["_global", "u_priya"], touched=True)
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["session"] == "meet-42" and rows[0]["title"] == "DNA TSC"
    assert rows[0]["workspaces"] == ["_global", "u_priya"] and rows[0]["touched"] is True
    # `meeting` is null for a session nobody bound one to — including this one, whose id NAMES the
    # meeting. `meet-<row>` is still the terminal's own naming and the client still reads the ref
    # back off the id; the field exists for the chat whose id names nothing (Vexa-ai/vexa#1597,
    # pinned in test_chat_meeting_binding.py).
    assert rows[0]["meeting"] is None and rows[0]["meeting_native"] is None


def test_has_history_is_internal_tier(client):
    r = client.get("/internal/has-history", params={"who": "priya@acme.test"})
    assert r.status_code == 403
    r = client.get("/internal/has-history", params={"who": "priya@acme.test"},
                   headers={"X-Internal-Secret": "wrong"})
    assert r.status_code == 403


def test_a_stranger_has_nothing_to_return_to(client):
    r = client.get("/internal/has-history", params={"who": "nobody@acme.test"},
                   headers={"X-Internal-Secret": INTERNAL})
    assert r.status_code == 200
    assert r.json()["has_history"] is False
    assert r.json()["sessions"] == 0 and r.json()["desk"] == "new"


def test_a_chat_thread_is_history(client, stack):
    """The founder's own case: sessions on the server, an empty rail in a new window. The arrival
    must see what the rail could not."""
    stack["sessions"].upsert("u_priya", "scaffold-SC1", title="Welcome")
    r = client.get("/internal/has-history", params={"who": "priya@acme.test"},
                   headers={"X-Internal-Secret": INTERNAL}).json()
    assert r["has_history"] is True and r["sessions"] == 1


def test_a_desk_with_something_in_it_is_history_too(client, stack):
    """The person who has never typed a word here and would still be wrong to greet as a stranger:
    a colleague put them in a meeting and the report landed on their desk."""
    entities = stack["root"] / "u_leo" / "kg" / "entities" / "meeting"
    entities.mkdir(parents=True)
    (entities / "2026-09-04-dna-tsc.md").write_text("# DNA TSC\n")
    r = client.get("/internal/has-history", params={"who": "leo@acme.test"},
                   headers={"X-Internal-Secret": INTERNAL}).json()
    assert r["sessions"] == 0 and r["desk"] != "new" and r["has_history"] is True


def test_who_must_be_an_address(client):
    r = client.get("/internal/has-history", params={"who": "u_priya"},
                   headers={"X-Internal-Secret": INTERNAL})
    assert r.status_code == 400


# ── 3. the opening rule is on first-visit too (#1583, pinned here) ───────────────────────────────

def test_the_first_visit_opening_leads_with_the_no_narration_rule(client, stack):
    """The walk that produced #1591 also opened with *"I'll start by getting oriented before I say
    anything to you."* — the pattern #1583 addressed. That fix put `OPENING_RULE` at the HEAD of
    every composed opening (`api._scaffold_view`), so this kind already has it; nothing here needed
    changing, and this is the test that says so out loud rather than a comment claiming it."""
    minted = client.post("/internal/scaffolds", headers={"X-Internal-Secret": INTERNAL},
                         json={"who": "priya@acme.test", "kind": "first-visit",
                               "opening": "first-visit",
                               "provenance": {"flow": "sign-in", "step": "first-visit"}})
    assert minted.status_code == 201
    sid = minted.json()["id"]
    view = client.get(f"/api/scaffolds/{sid}",
                      headers={"X-User-Id": "u_priya", "X-User-Email": "priya@acme.test"}).json()
    assert view["opening_text"].startswith(scaffolds_mod.OPENING_RULE)
    assert "Never open with what you are about to do" in view["opening_text"]
