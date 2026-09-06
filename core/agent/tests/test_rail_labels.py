"""THE RAIL SHOWS A NAME, NEVER MACHINERY (Vexa-ai/vexa#1602).

The founder's rail, 2026-09-06 12:50Z, after #1591 made it the server's sessions rather than one
browser's storage. Eleven rows, and this is the list, verbatim:

    Active context: the u…   (×4)      welcome
    [vexa-job:extend…                  Workspace setup
    [minutes-review…                   what's my comp…
    [prep] They click…                 setup global
                                       Google Me… held
                                       DNA TSC 20… held

The right-hand column is what a rail is for. The left-hand one is the terminal's "Active context:
the user is viewing…" preamble, a job mark and two asks' `[kind]` prefixes — **a person never typed
any of it.** A server-derived row was labelled with the session's FIRST USER TEXT, and the first
user text of most sessions is machinery.

The rule, which is `shared/chat_label.py` and is the same one on every client because the server
computes it: the meeting's title → the scaffold's label → the act label → the person's own first
words with every machinery preamble stripped. Never a bracket, never a mark, never "Active context".

WHAT THESE TESTS FEED. The founder's rows as the INDEX holds them — `_truncate_title(prompt)`, 60
characters, single-lined — because that is what `/api/sessions` has to name today, and separately
the whole composed prompt, because that is what the mint path names tomorrow. The two are different
strings and only one of them still contains the person's sentence, which is the whole reason the
fix has two halves.

The client half is pinned in `clients/terminal/src/minutes/__tests__/railLabels.test.ts`.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import chat_label
from shared.config import load_settings
from shared.marks import MACHINERY_MARK, PHASE_MARK, job_mark

INTERNAL = "internal-tier-secret-for-tests"

# ── the founder's rail, as the index holds it ────────────────────────────────────────────────────
# Each of these is `_truncate_title(<the composed prompt>)`: one line, cut at 60. The cut is the
# reason two of them cannot be repaired into a sentence — it landed inside the machinery.
STORED_ACTIVE_CONTEXT = "Active context: the user is viewing the workspace file kg/e…"
STORED_JOB = "[vexa-job:extend:personal/kg/entities/person/james-spadafo…"
STORED_MINUTES = "[minutes-review] Someone clicked through from an extract em…"
STORED_PREP = "[prep] They clicked through from a prepare email about **DN…"

# …and the same Active-context turn as it reaches the ROUTE, before anything cuts it.
LIVE_ACTIVE_CONTEXT = (
    "Active context: the user is viewing the workspace file kg/entities/person/"
    "james-spadafora.md. Read it with your Read tool if relevant.\n\n---\nwhat did we decide?")

PREP_ASK = """---
label: prepare
mounts: _global, personal
---
[prep] They clicked through from a prepare email about **{{title}}**, {{when}}.
"""

MINUTES_ASK = """---
label: minutes
mounts: _global, personal
---
[minutes-review] Someone clicked through from an extract email to read the minutes of {{meeting}}.
"""

EXTEND_ASK = """---
label: extend
mounts: personal, _global
---
[extend] They pressed Extend on `{{path}}` in `{{workspace}}`.
"""


# ── 1. the rule, on its own ──────────────────────────────────────────────────────────────────────

def test_the_persons_words_survive_the_terminals_active_context_block():
    """The live shape — everything before the `---` is the terminal narrating what is on screen."""
    assert chat_label.human_head(LIVE_ACTIVE_CONTEXT) == "what did we decide?"
    assert chat_label.chat_label(LIVE_ACTIVE_CONTEXT) == "what did we decide?"


def test_a_single_lined_active_context_block_is_stripped_too():
    """`_truncate_title` collapses newlines, so a STORED title separates on ` --- `, not `\\n\\n---\\n`.
    A rule that only knew the live shape would leave every stored row exactly as the founder saw it."""
    flat = " ".join(LIVE_ACTIVE_CONTEXT.split())
    assert chat_label.chat_label(flat) == "what did we decide?"


def test_a_block_the_cut_landed_inside_yields_no_name_rather_than_a_wrong_one():
    """The preamble alone is 55 characters and the index stores 60, so this row's sentence is gone.
    Empty is the honest answer — the client renders its own placeholder — and it is emphatically not
    the old answer, which was to paint the machinery on the rail."""
    assert chat_label.chat_label(STORED_ACTIVE_CONTEXT) == ""
    assert chat_label.human_head(STORED_ACTIVE_CONTEXT) == ""


def test_an_act_is_named_by_what_the_person_pressed():
    whole = job_mark("extend", "personal/kg/entities/person/james-spadafora.md") + "[extend] They pressed…"
    assert chat_label.chat_label(whole) == "Extend: personal/kg/entities/person/james-spadafora.md"


def test_an_act_whose_mark_the_cut_bisected_is_still_named():
    """`marks.read_job_mark` refuses a mark with no closing `]` and must keep refusing — it reads the
    RECORD. This reads a string that has already been truncated, which is a display problem, and it
    is the only reason the founder's Extend row can carry a name at all."""
    assert chat_label.chat_label(STORED_JOB) == "Extend: personal/kg/entities/person/james-spadafo…"


def test_the_ask_that_composed_a_prompt_names_itself_in_its_first_bracket():
    """How a row minted before the terminal rode its scaffold id onto the first turn is still named
    by its record: the caller looks this kind up in the ask library and passes back `label:`."""
    assert chat_label.preset_kind(STORED_PREP) == "prep"
    assert chat_label.preset_kind(STORED_MINUTES) == "minutes-review"
    assert chat_label.preset_kind("what's my company called?") == ""
    assert chat_label.chat_label(STORED_PREP, scaffold_label="prepare") == "prepare"


def test_the_meeting_outranks_everything_including_a_machinery_title():
    assert chat_label.chat_label(STORED_PREP, meeting_title="DNA TSC 2026-09-04",
                                 scaffold_label="prepare") == "DNA TSC 2026-09-04"


def test_marks_never_reach_a_label():
    silent = MACHINERY_MARK + " " + PHASE_MARK + " [highlight] They pressed Highlight."
    assert chat_label.chat_label(silent) == "They pressed Highlight."
    for label in (STORED_JOB, STORED_ACTIVE_CONTEXT, STORED_PREP, MACHINERY_MARK + " x"):
        assert chat_label.is_machinery_label(label) is True
    for label in ("welcome", "Workspace setup", "what's my company called?", "setup global",
                  "DNA TSC 2026-09-04", "Extend: personal/kg/entities/person/ada.md"):
        assert chat_label.is_machinery_label(label) is False


def test_a_name_a_person_chose_is_never_touched():
    for name in ("welcome", "Workspace setup", "what's my company called?", "setup global"):
        assert chat_label.chat_label(name) == name


def test_a_label_is_one_line_and_cut_where_the_index_cuts():
    long = "x" * 200
    out = chat_label.chat_label(long)
    assert len(out) == chat_label.CHAT_LABEL_MAX and out.endswith("…")
    assert chat_label.chat_label("  two   lines\nof it ") == "two lines of it"


def test_an_empty_prompt_is_an_empty_label_not_a_word_of_ours():
    """"Chat" belongs to the client (`minutes/chats.ts` — `isPlaceholderLabel`). A server that
    shipped it would hand every client a name that outranks the reader's own rename in the merge."""
    assert chat_label.chat_label("") == "" and chat_label.chat_label("   ") == ""


# ── 2. the HTTP surface: /api/sessions carries the label ─────────────────────────────────────────

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


MEETING_ROWS = [
    {"id": 38, "status": "completed", "platform": "google_meet", "native_meeting_id": "dna-tsc-001",
     "data": {"title": "DNA TSC 2026-09-04"}},
    # the meeting nobody gave a title: `data.title` is absent, so the rule falls THROUGH to the
    # session's own — a platform-and-code rendering is each client's, not a name the server holds.
    {"id": 41, "status": "completed", "platform": "google_meet", "native_meeting_id": "cqb-egsq-vmt",
     "data": {}},
]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    asks = root / "_global" / "asks"
    asks.mkdir(parents=True)
    (asks / "prep.md").write_text(PREP_ASK)
    (asks / "minutes-review.md").write_text(MINUTES_ASK)
    (asks / "extend.md").write_text(EXTEND_ASK)
    return {"root": root, "sessions": _Sessions()}


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
                     schedule_source=lambda subject: list(MEETING_ROWS))
    return TestClient(app)


def _labels(client) -> "dict[str, str]":
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_dmitry"}).json()["sessions"]
    return {r["session"]: r["label"] for r in rows}


def test_the_founders_rail_reads_as_names(client, stack):
    """THE ACCEPTANCE. His eleven rows, as the index holds them, through the route he loads."""
    sess = stack["sessions"]
    for i in range(4):
        sess.upsert("u_dmitry", f"pchat-ctx{i}", title=STORED_ACTIVE_CONTEXT)
    sess.upsert("u_dmitry", "pchat-job", title=STORED_JOB)
    sess.upsert("u_dmitry", "pchat-min", title=STORED_MINUTES)
    sess.upsert("u_dmitry", "pchat-prep", title=STORED_PREP)
    sess.upsert("u_dmitry", "scaffold-SC1", title="welcome")
    sess.upsert("u_dmitry", "pchat-ws", title="Workspace setup")
    sess.upsert("u_dmitry", "pchat-comp", title="what's my company called?")
    sess.upsert("u_dmitry", "scaffold-SC2", title="setup global")
    sess.upsert("u_dmitry", "meet-41", title="Google Meet · cqb-egsq-vmt")
    sess.upsert("u_dmitry", "meet-38", title=STORED_PREP)

    labels = _labels(client)
    # the four rows the cut emptied: no name is recoverable, and none of them shows machinery
    for i in range(4):
        assert labels[f"pchat-ctx{i}"] == ""
    # the act, the two asks
    assert labels["pchat-job"] == "Extend: personal/kg/entities/person/james-spadafo…"
    assert labels["pchat-min"] == "minutes"
    assert labels["pchat-prep"] == "prepare"
    # the rows that were already names — unchanged, to the character
    assert labels["scaffold-SC1"] == "welcome"
    assert labels["pchat-ws"] == "Workspace setup"
    assert labels["pchat-comp"] == "what's my company called?"
    assert labels["scaffold-SC2"] == "setup global"
    # the meetings: the one a person titled takes that title even over a machinery row title; the
    # one nobody titled keeps the session's own, because the server holds no better answer
    assert labels["meet-38"] == "DNA TSC 2026-09-04"
    assert labels["meet-41"] == "Google Meet · cqb-egsq-vmt"
    # …and the whole rail, as one statement
    assert not any(chat_label.is_machinery_label(v) for v in labels.values())


def test_title_is_untouched_so_every_existing_consumer_still_reads_it(client, stack):
    """`label` is added BESIDE `title`, never over it. The index stores what it stored."""
    stack["sessions"].upsert("u_dmitry", "pchat-prep", title=STORED_PREP)
    row = client.get("/api/sessions", headers={"X-User-Id": "u_dmitry"}).json()["sessions"][0]
    assert row["title"] == STORED_PREP and row["label"] == "prepare"


def test_a_chat_that_MADE_a_meeting_keeps_its_own_name(client, stack):
    """Vexa-ai/vexa#1597, and the one place this rule and that one could have collided. A chat that
    CREATED a meeting was a conversation first and the person's own sentence named it — the founder
    asked for the meeting's STATUS on that row, not for the row to become something else. Only a
    chat BORN as a meeting's (`meet-<row>`) is named by the meeting."""
    stack["sessions"].upsert("u_dmitry", "pchat-abc", title="send a bot to the DNA call")
    stack["sessions"].upsert("u_dmitry", "pchat-abc", meeting="38", meeting_native="dna-tsc-001")
    assert _labels(client)["pchat-abc"] == "send a bot to the DNA call"


def test_a_meetings_domain_that_is_down_costs_the_rail_its_meeting_names_and_nothing_else(stack):
    def boom(subject):
        raise OSError("meeting-api down")

    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL, ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_FakeReader(),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"], schedule_source=boom)
    stack["sessions"].upsert("u_dmitry", "meet-38", title="Google Meet · dna-tsc-001")
    rows = TestClient(app).get("/api/sessions", headers={"X-User-Id": "u_dmitry"}).json()["sessions"]
    assert rows[0]["label"] == "Google Meet · dna-tsc-001"


# ── 3. the mint path: a row is named before it is cut ────────────────────────────────────────────

def _turn(client, session, prompt, **body):
    return client.post("/api/chat", json={"prompt": prompt, "session": session, **body},
                       headers={"X-User-Id": "u_dmitry"})


def test_a_new_row_is_titled_with_the_persons_sentence_not_the_preamble(client, stack):
    """The half a list-time rule cannot do: here the whole prompt still exists, so the cut lands on
    the sentence rather than inside the machinery in front of it."""
    assert _turn(client, "pchat-new", LIVE_ACTIVE_CONTEXT).status_code == 200
    assert stack["sessions"].list("u_dmitry")[0]["title"] == "what did we decide?"


def test_an_act_titles_its_row_with_the_button_that_opened_it(client, stack):
    """#1588's ruling, one surface along: the person pressed Extend and the row says Extend. The
    ask's own `label:` ("extend") is NOT used here — `Extend: <path>` says more."""
    assert _turn(client, "pchat-act", "make it fuller",
                 intent={"kind": "extend", "workspace": "personal",
                         "path": "kg/entities/person/ada.md"}).status_code == 200
    assert stack["sessions"].list("u_dmitry")[0]["title"] == \
        "Extend: personal/kg/entities/person/ada.md"


def test_a_machinery_title_is_replaced_by_the_next_turn(client, stack):
    """A stored title the rule REFUSES is not a name, so the write-once latch does not protect it.
    This is what heals the founder's four `Active context: the u…` rows in place."""
    stack["sessions"].upsert("u_dmitry", "pchat-old", title=STORED_ACTIVE_CONTEXT)
    assert _turn(client, "pchat-old", "and what about pricing?").status_code == 200
    assert stack["sessions"].list("u_dmitry")[0]["title"] == "and what about pricing?"
    assert _labels(client)["pchat-old"] == "and what about pricing?"


def test_a_name_already_on_a_row_is_still_written_once_and_never_again(client, stack):
    """Including a rename a person made. The latch is relaxed for machinery, not removed."""
    stack["sessions"].upsert("u_dmitry", "pchat-named", title="Pricing for Kaar")
    assert _turn(client, "pchat-named", "and what about the DNA call?").status_code == 200
    assert stack["sessions"].list("u_dmitry")[0]["title"] == "Pricing for Kaar"
