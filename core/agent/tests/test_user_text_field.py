"""F47/F51 + decision 22 — what the person said is a FIELD, not something a reader reconstructs.

THE REGRESSION. Chat history is read out of the harness transcript, which stores the prompt the CLI
was GIVEN: the worker's voice/kg-links/mount-stack/entity-index/global-context preambles, then the
control plane's grounding, then the sentence somebody typed. The terminal rebuilt the human half by
STRIPPING all of that off the front — one cut at the control plane's sentinel when there was one,
otherwise regexes written against the preambles' own wording.

On 2026-09-02 the preamble set changed shape. The founder's turns carried no meeting, schedule or
workspace grounding, so the control plane folded nothing and wrote no sentinel; the fallback regexes
no longer matched the preambles; and every stored turn in his chat rendered as a grey USER bubble
opening `## Referencing knowledge (always)`, followed by the mount stack and the write-routing
policy, with his own question at the bottom.

Reconstruction by stripping cannot be made safe — it can only be made unnecessary. These tests pin
the three moves that made it unnecessary: the worker records the human half beside the continuity
pointer, the history reader serves it as `user_text`, and the sentinel is now written on every turn
that carries a person's words rather than on the shapes that happened to fold something in.
"""
from __future__ import annotations

import json
from pathlib import Path

from control_plane import api as control_api
from control_plane.workspace_reader import WorkspaceReader
from worker import engine


# ── the boundary marker: one literal, three languages ────────────────────────────────────────────

def test_worker_and_control_plane_agree_on_the_sentinel():
    """The worker cannot import the control plane (separate image), so the literal is duplicated.
    A rename on either side must be a failing test here, not a silent history regression."""
    assert engine.CONTEXT_SENTINEL == control_api.CONTEXT_SENTINEL


def test_human_half_cuts_at_the_sentinel_and_nowhere_else():
    grounded = ("<schedule tz=\"Europe/Lisbon\">…</schedule>\n\n"
                + engine.CONTEXT_SENTINEL + "what did we decide?")
    assert engine.human_half(grounded) == "what did we decide?"


def test_human_half_of_an_ungrounded_turn_is_the_whole_prompt():
    """No sentinel means the control plane folded nothing in front of the person — so the prompt IS
    their words. This is the exact shape whose history broke: the worker's own preambles are added
    AFTER this, so nothing else in the pipeline can tell the two halves apart."""
    assert engine.human_half("prepare me for the TSC call") == "prepare me for the TSC call"


# ── the record, and the reader that serves it ────────────────────────────────────────────────────

def _transcript(ws: Path, sid: str, lines: list[dict]) -> None:
    proj = ws / ".claude" / "projects" / "-some-cwd-slug"
    proj.mkdir(parents=True, exist_ok=True)
    proj.joinpath(f"{sid}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in lines))


def _thread(root: Path, subject: str, session: str = "main", sid: str = "sid-1") -> Path:
    ws = root / subject
    (ws / ".claude" / "sessions").mkdir(parents=True)
    (ws / ".claude" / "sessions" / f"{session}.session").write_text(sid + "\n")
    return ws


PREAMBLES = (
    "## Referencing knowledge (always)\n\nrules — or use plain text.\n\n"
    "## Your mounted workspaces\n\nThis turn mounts a STACK of workspaces\n"
    "Write-routing policy:\n- never write a read-only mount\n\n"
)


def test_history_serves_the_person_words_as_their_own_field(tmp_path):
    ws = _thread(tmp_path, "u_dmitry")
    composed = PREAMBLES + engine.CONTEXT_SENTINEL + "what did we decide about the licence?"
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": composed}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "You decided…"}]}},
    ])
    engine.record_user_text(ws, "main", composed, "what did we decide about the licence?")

    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["user_text"] == "what did we decide about the licence?"
    # `text` stays the stored prompt — the terminal's shape filters read the RECORD, not the sentence
    assert turns[0]["text"] == composed


def test_the_field_survives_a_preamble_nothing_recognises(tmp_path):
    """The whole point: the reader never looks at the machinery, so a preamble invented next month
    cannot put itself in a grey user bubble."""
    ws = _thread(tmp_path, "u_dmitry")
    composed = "## A preamble from next month\n\nnovel wording, no sentinel\nprepare me for the TSC call"
    _transcript(ws, "sid-1", [{"type": "user", "message": {"role": "user", "content": composed}}])
    engine.record_user_text(ws, "main", composed, "prepare me for the TSC call")

    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert turns[0]["user_text"] == "prepare me for the TSC call"


def test_a_record_written_before_the_field_carries_no_user_text(tmp_path):
    """Old turns are not rewritten — they are the users' own records. They arrive without the field
    and the terminal's strip stays their fallback."""
    ws = _thread(tmp_path, "u_dmitry")
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": PREAMBLES + "an older question"}},
    ])
    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert turns == [{"role": "user", "text": PREAMBLES + "an older question"}]


def test_the_sidecar_is_capped_and_keeps_the_newest(tmp_path):
    ws = _thread(tmp_path, "u_dmitry")
    for i in range(engine.USER_TEXT_KEEP + 5):
        engine.record_user_text(ws, "main", f"composed-{i}", f"said-{i}")
    lines = (ws / ".claude" / "sessions" / "main.turns.jsonl").read_text().splitlines()
    assert len(lines) == engine.USER_TEXT_KEEP
    assert json.loads(lines[-1])["user_text"] == f"said-{engine.USER_TEXT_KEEP + 4}"


def test_recording_never_takes_down_a_turn(tmp_path):
    """A sidecar that cannot be written costs the turn nothing — history just falls back."""
    blocked = tmp_path / "nope"
    blocked.write_text("this is a file, not a workspace directory")
    engine.record_user_text(blocked, "main", "composed", "said")   # must not raise


# ── decision 22 — the private baseline is a DESK when it is described to anyone ──────────────────

def test_the_mount_description_says_desk():
    """The founder read `**seed** (your PRIVATE baseline …)` back in his own chat. A seed is what the
    platform copied to make the thing, not what the thing IS. Only the human-facing description
    moved; the slug and the path still say whatever they say."""
    mounts = [
        {"slug": "seed", "path": "/workspaces/127", "role": "private", "write": True, "primary": True},
        {"slug": "_system", "path": "/workspaces/_system", "role": "system", "write": True},
    ]
    text = engine.mounts_preamble(mounts)
    assert "your DESK" in text
    assert "PRIVATE baseline (durable personal memory)" not in text
    assert "`/workspaces/127`" in text          # the path is untouched
    assert "**seed**" in text                   # …and so is the slug


# ── F51 — the write-back phase is bookkeeping, not conversation ─────────────────────────────────

def test_the_writeback_prompt_declares_itself_in_the_record():
    prompt = engine.writeback_prompt(["Marvin Ostroff"])
    assert engine.WRITEBACK_MARK in prompt
    assert engine.MACHINERY_MARK in prompt


def test_a_phase_exchange_never_renders_as_conversation(tmp_path):
    """The founder read his prepare chat back as: an empty agent card, a USER bubble saying
    "Continue from where you left off.", then "No response requested — write-back complete." All
    three are the write-back phase and the harness's own nudge, in the same CLI session as his turn.
    What he actually said, and what the agent actually answered, are the only two turns left."""
    ws = _thread(tmp_path, "u_dmitry")
    composed = PREAMBLES + engine.CONTEXT_SENTINEL + "prepare me for the DNA call"
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": composed}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "Here is the brief."}]}},
        # the phase, in the same session
        {"type": "user", "message": {"role": "user", "content": engine.writeback_prompt(["Marvin"])}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": ""}]}},
        {"type": "user", "message": {"role": "user", "content": "Continue from where you left off."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "No response requested — write-back complete."}]}},
    ])
    engine.record_user_text(ws, "main", composed, "prepare me for the DNA call")

    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["user_text"] == "prepare me for the DNA call"
    assert turns[1]["text"] == "Here is the brief."
    rendered = json.dumps(turns)
    assert "Continue from where you left off" not in rendered
    assert "No response requested" not in rendered


def test_the_harness_nudge_rejoins_the_answer_it_interrupted(tmp_path):
    """The auto-continue is a user line nobody typed. Dropping it WITHOUT closing the open agent turn
    also puts the interrupted reply back together — one answer, not two halves with machinery
    between them."""
    ws = _thread(tmp_path, "u_dmitry")
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": "how did the call go?"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "It went "}]}},
        {"type": "user", "message": {"role": "user", "content": "Continue from where you left off."}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "well."}]}},
    ])
    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[1]["text"] == "It went well."


def test_an_agent_turn_with_nothing_in_it_is_not_a_turn(tmp_path):
    ws = _thread(tmp_path, "u_dmitry")
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "   "}]}},
    ])
    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert [t["role"] for t in turns] == ["user"]


# ── the boundary marker is written on EVERY turn that carries a person's words ───────────────────

def test_the_sentinel_is_written_even_when_the_control_plane_folded_nothing(monkeypatch):
    """IT USED TO SKIP EXACTLY THE TURNS THAT NEEDED IT. The condition carried `len(prompt) >
    len(body.prompt)` — "only mark the boundary when I actually folded something" — but the WORKER
    prepends its own preambles after this returns, so a plain turn still reaches the transcript with
    screens of machinery in front of the sentence and no marker to say where it starts. That is the
    2026-09-02 shape: no meeting, no schedule, no workspace context, therefore no sentinel,
    therefore the terminal fell through to regexes that no longer matched."""
    from fastapi.testclient import TestClient

    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from shared.config import load_settings
    from tests.test_api import _FakeIdentity, _FakeReader, _FakeRuntime

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    runtime = _FakeRuntime()
    c = TestClient(create_app(
        Dispatcher(load_settings(), runtime, _FakeIdentity()), stream_reader=_FakeReader(),
    ))

    r = c.post("/api/chat", json={"prompt": "what did we decide?", "subject": "u_dmitry",
                                 "session": "s1"})
    assert r.status_code == 200

    start = next(env["VEXA_START"] for _wid, _profile, env in runtime.spawned if "VEXA_START" in env)
    dispatched = json.loads(start)["entrypoint"]["inline"]
    assert dispatched.endswith(control_api.CONTEXT_SENTINEL + "what did we decide?")
    # …and the worker reads exactly the person's half back out of it
    assert engine.human_half(dispatched) == "what did we decide?"


# ── F53 — `(unset)` is a gap, never a fact ───────────────────────────────────────────────────────

def test_an_unfilled_seed_page_announces_itself_in_the_same_read(tmp_path):
    """The founder's group workspace was created before the template-free seed, so its README still
    carries the seed's `(unset)` fields — and the agent reported "the project's objective is still
    `(unset)`" as though that were a finding. Those pages have no frontmatter, so the
    `template: true` rule cannot reach them; the bytes have to say what they are."""
    ws = tmp_path / "aswf-dna-project-b7b2ee"
    ws.mkdir(parents=True)
    ws.joinpath("README.md").write_text("# DNA project\n\nObjective: (unset)\nOwner: (unset)\n")

    text = WorkspaceReader(str(tmp_path)).read("aswf-dna-project-b7b2ee", "README.md")
    assert text.startswith("UNFILLED")
    assert "never a value" in text
    assert "# DNA project" in text            # the page itself is still there, unchanged


def test_an_unfilled_page_stays_visible_in_the_tree(tmp_path):
    """Unlike a TEMPLATE, which should never have been enumerable, this is a real page of theirs
    waiting to be filled in. Hiding it would take a person's own README away from them."""
    ws = tmp_path / "aswf-dna-project-b7b2ee"
    ws.mkdir(parents=True)
    ws.joinpath("README.md").write_text("Objective: (unset)\n")
    assert WorkspaceReader(str(tmp_path)).tree("aswf-dna-project-b7b2ee") == ["README.md"]


def test_a_filled_page_is_returned_untouched(tmp_path):
    ws = tmp_path / "acme"
    ws.mkdir(parents=True)
    ws.joinpath("README.md").write_text("Objective: ship 0.13\n")
    assert WorkspaceReader(str(tmp_path)).read("acme", "README.md") == "Objective: ship 0.13\n"


def test_every_composed_opening_carries_the_gap_rule():
    from control_plane import scaffolds
    assert "`(unset)`" in scaffolds.MACHINERY_NOTE
    assert "GAP, never a fact" in scaffolds.MACHINERY_NOTE
