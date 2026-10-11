"""A CHAT'S NAME NEVER SHOWS THE USER-INPUT MARKER (Vexa-ai/vexa#1641).

Seen on the dogfood rail and in the delete dialog: a row called
``<!--vexa:user-input-below-->zz-delete-repro-8…``. The terminal's composer puts that marker right
in front of the person's sentence (`clients/terminal/src/surfaces/chat.tsx`), so it arrives inside
``body.prompt``; the naming rule stripped the "Active context…" block up to ``---`` and stopped, and
the marker became the first characters of the row's title.

What this pins: a title is the person's own words — what follows the marker — and any
``<!--vexa:…-->`` marker is gone from every label: a fresh turn's title, a title the index already
stored (cleaned on read, not rewritten), and a name sent to ``/api/chat/name``.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import api_shared
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import chat_label
from shared.config import load_settings
from worker import engine

INTERNAL = "internal-tier-secret-for-tests"
SENTINEL = "<!--vexa:user-input-below-->"
USER = {"X-User-Id": "u_dmitry"}

# what the terminal's composer sends for a turn typed with a file open (chat.tsx `composePrompt`)
COMPOSED = ("Active context: the user is viewing the workspace file README.md. Read it with your "
            "Read tool if relevant.\n\n---\n" + SENTINEL + "zz-delete-repro-8 please remove this chat")
# …and the row the dogfood rail showed, as the index stored it before the fix
STORED = SENTINEL + "zz-delete-repro-8 please remove this ch…"


def test_the_three_spellings_of_the_marker_are_one_literal():
    assert chat_label.USER_INPUT_SENTINEL == api_shared.CONTEXT_SENTINEL == engine.CONTEXT_SENTINEL


def test_a_composed_turn_is_named_by_the_words_after_the_marker():
    assert chat_label.chat_label(COMPOSED) == "zz-delete-repro-8 please remove this chat"


def test_a_bare_marked_turn_is_named_by_the_person():
    assert chat_label.chat_label(SENTINEL + "who is this?") == "who is this?"


def test_a_stored_title_carrying_the_marker_reads_without_it():
    assert chat_label.chat_label(STORED) == "zz-delete-repro-8 please remove this ch…"


def test_no_vexa_marker_survives_any_label():
    for raw in ("<!-- vexa:anything -->hello", "hello <!--vexa:x-->world", "hi <!--vexa:user-inp"):
        out = chat_label.chat_label(raw)
        assert "<!--" not in out and "vexa:" not in out, out
    assert chat_label.strip_markers("<!--vexa:x-->Plan  the\nlaunch") == "Plan the launch"


def test_a_marker_title_is_machinery_so_the_next_turn_renames_it():
    assert chat_label.is_machinery_label(STORED)


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


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    return {"root": root, "sessions": _Sessions()}


@pytest.fixture
def client(stack):
    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL, ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_FakeReader(), reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"], schedule_source=lambda subject: [])
    return TestClient(app)


def _labels(client) -> "dict[str, str]":
    rows = client.get("/api/sessions", headers=USER).json()["sessions"]
    return {r["session"]: r["label"] for r in rows}


def test_a_new_chat_is_titled_from_the_persons_words(client, stack):
    r = client.post("/api/chat", json={"prompt": COMPOSED, "session": "pchat-new"}, headers=USER)
    assert r.status_code == 200
    row = stack["sessions"].list("u_dmitry")[0]
    assert row["title"] == "zz-delete-repro-8 please remove this chat"
    assert _labels(client)["pchat-new"] == "zz-delete-repro-8 please remove this chat"


def test_a_stored_marker_title_is_cleaned_on_read_and_left_as_stored(client, stack):
    stack["sessions"].upsert("u_dmitry", "pchat-old", title=STORED)
    assert _labels(client)["pchat-old"] == "zz-delete-repro-8 please remove this ch…"
    assert stack["sessions"].list("u_dmitry")[0]["title"] == STORED


def test_a_stored_marker_name_is_cleaned_on_read(client, stack):
    stack["sessions"].upsert("u_dmitry", "pchat-named", title="x")
    stack["sessions"].name("u_dmitry", "pchat-named", SENTINEL + "Delete repro", human=False)
    assert _labels(client)["pchat-named"] == "Delete repro"


def test_a_name_sent_with_the_marker_is_stored_without_it(client, stack):
    stack["sessions"].upsert("u_dmitry", "pchat-n", title="x")
    r = client.post("/api/chat/name/agent", json={"session": "pchat-n",
                                                   "title": SENTINEL + "Clean up test chats"},
                    headers=USER)
    assert r.status_code == 200 and r.json()["label"] == "Clean up test chats"
    assert stack["sessions"].list("u_dmitry")[0]["named_title"] == "Clean up test chats"
