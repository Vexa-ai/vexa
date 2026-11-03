"""The delivery gate's in-app kinds (PRD 0.13.4 S6, D15): flows names a card that waits on the person
over the internal tier; agent-api writes the row and composes the words from two names. The gate then
reads whether the person has read it (deny test 30's room half): a read mention suppresses its email."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from control_plane import notifications as n


class _Store:
    def __init__(self):
        self._c = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        self._c.execute("CREATE TABLE agent_session_notification (id TEXT PRIMARY KEY, recipient TEXT, kind TEXT, "
                        "actor TEXT, actor_name TEXT, object_kind TEXT, object_id TEXT, object_name TEXT, url TEXT, "
                        "text TEXT, created REAL, read_at REAL, acted TEXT, source TEXT, expires_at REAL)")

    @contextmanager
    def _tx(self):
        with self._lock:
            yield
            self._c.commit()

    def _x(self, sql, params=()):
        return self._c.execute(sql, params)


def test_a_card_waiting_notice_is_two_names_and_nothing_else():
    note = n.Notifier(_Store())
    [row] = note.emit(["126"], kind="card_waiting", actor="vexa", actor_name="Dmitry", object_kind="card",
                      object_id="c1", object_name="Interview guide reviewed", url="/decide/c1")
    assert row["text"] == "Dmitry is waiting on you: “Interview guide reviewed”"
    assert row["object"] == {"kind": "card", "id": "c1", "name": "Interview guide reviewed", "url": "/decide/c1"}
    assert set(n.NUDGE_KINDS) <= set(n.KINDS) and set(n.NUDGE_KINDS) <= set(n.INTERNAL_KINDS)


def test_read_state_answers_only_the_recipients_own_rows():
    note = n.Notifier(_Store())
    [mine] = note.emit(["126"], kind="card_waiting", actor="vexa", actor_name="Ana", object_kind="card",
                       object_id="c1", object_name="Sign")
    [theirs] = note.emit(["200"], kind="card_waiting", actor="vexa", actor_name="Ana", object_kind="card",
                         object_id="c2", object_name="Sign")
    assert note.state("126", [mine["id"], theirs["id"]]) == {mine["id"]: {"read": False, "acted": None}}
    note.mark_read("126", [mine["id"]])
    assert note.state("126", [mine["id"]])[mine["id"]]["read"] is True
    assert note.state("126", []) == {}


# ── the internal doors, end to end (FastAPI over the sqlite session store) ──────────────────────
from tests.test_notifications_routes import JOE, SECRET, _mine, env  # noqa: E402,F401


def _post(client, path="/internal/notifications", secret=SECRET, **body):
    return client.post(path, json=body, headers={"x-internal-secret": secret} if secret else {})


def test_flows_writes_a_card_waiting_row_and_reads_back_whether_it_was_read(env):
    client, *_ = env
    r = _post(client, kind="card_waiting", to=JOE, object_id="c1", object_kind="card",
              title="Interview guide reviewed", actor_name="Dmitry", url="/decide/c1")
    assert r.status_code == 200, r.text
    [nid] = r.json()["ids"]
    [row] = _mine(client, JOE)["notifications"]
    assert row["text"] == "Dmitry is waiting on you: “Interview guide reviewed”" and row["object"]["url"] == "/decide/c1"
    st = _post(client, "/internal/notifications/state", to=JOE, ids=[nid, "n-not-theirs"])
    assert st.status_code == 200 and st.json() == {"notices": {nid: {"read": False, "acted": None}}}
    assert _post(client, "/internal/notifications/state", secret="wrong", to=JOE, ids=[nid]).status_code == 403


def test_a_nudge_kind_names_a_card_and_a_meeting_kind_a_meeting(env):
    client, *_ = env
    assert _post(client, kind="card_waiting", to=JOE, meeting_id="7", title="x").status_code == 422
    assert _post(client, kind="bot_joined", to=JOE, object_id="c1", object_kind="card", title="x").status_code == 422
    assert _post(client, kind="card_waiting", to=JOE, object_id="c1", title="x",
                 url="https://evil.example/x").status_code == 422
