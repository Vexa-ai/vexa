"""worker harness — the redis serve() loop over a fake stream + injected turn (no docker, no claude).

Proves: the entrypoint turn runs first, each turn's UnitEvents XADD to the output Stream tagged with a
turn id + a turn-complete marker, interactive messages on the input Stream run in order, a `stop`
message exits, and an idle read reaps the harness (returns).
"""
from __future__ import annotations

import json
import pathlib

from agent_api.worker import serve, _link_skills_into_workspace


class FakeStream:
    def __init__(self, inbox=None):
        self.out = []  # (topic, fields)
        self._inbox = list(inbox or [])

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        in_topic = next(iter(streams))
        if not self._inbox:
            return []  # idle → serve() returns
        eid, fields = self._inbox.pop(0)
        return [(in_topic, [(eid, fields)])]

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


def _turn(prompt):
    yield {"type": "message-delta", "text": f"re:{prompt}"}
    yield {"type": "commit", "sha": "abc"}


def _msg(eid, prompt):
    return (eid, {"turn": json.dumps({"prompt": prompt})})


def test_entrypoint_then_interactive_then_idle():
    s = FakeStream(inbox=[_msg("1-0", "again")])
    serve(s, out_topic="unit:u:out", in_topic="unit:u:in", turn=_turn,
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    evs = s.events()
    # t0 (entrypoint "hello"): delta, commit, turn-complete
    assert evs[0] == {"type": "message-delta", "text": "re:hello", "turn_id": "t0"}
    assert evs[1]["type"] == "commit" and evs[1]["turn_id"] == "t0"
    assert evs[2] == {"type": "turn-complete", "turn_id": "t0"}
    # t1 (interactive "again")
    assert evs[3] == {"type": "message-delta", "text": "re:again", "turn_id": "t1"}
    assert evs[5] == {"type": "turn-complete", "turn_id": "t1"}
    assert all(t == "unit:u:out" for t, _ in s.out)


def test_session_start_serves_inbox_without_entrypoint_turn():
    s = FakeStream(inbox=[_msg("1-0", "hi")])
    serve(s, out_topic="o", in_topic="i", turn=_turn,
          start={"session": {"ref": ".claude/.session"}}, idle_ms=10)
    evs = s.events()
    # no t0 — the first event is the interactive turn t1
    assert evs[0]["turn_id"] == "t1"


def test_stop_message_exits_immediately():
    s = FakeStream(inbox=[("1-0", {"turn": json.dumps({"type": "stop"})}), _msg("2-0", "never")])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={}, idle_ms=10)
    assert s.out == []  # stop before any turn ran


# ── meeting mode: consume transcript Stream → gate → emit cards ───────────────────────────────────

from agent_api.worker import meeting_card_turn, parse_cards, parse_notes, serve_meeting  # noqa: E402


class MeetingStream:
    def __init__(self, inbox):
        self.out = []
        self._inbox = list(inbox)

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        tname = next(iter(streams))
        if not self._inbox:
            return []
        batch, self._inbox = self._inbox, []
        return [(tname, list(batch))]

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


class StepMeetingStream(MeetingStream):
    def xread(self, streams, count=1, block=None):
        tname = next(iter(streams))
        if not self._inbox:
            return []
        entry = self._inbox.pop(0)
        return [(tname, [entry])]


class CursorMeetingStream(MeetingStream):
    def __init__(self, inbox):
        super().__init__(inbox)
        self.cursors = []

    def xread(self, streams, count=1, block=None):
        self.cursors.append(dict(streams))
        return super().xread(streams, count=count, block=block)


def _seg(speaker, text, completed=True):
    return {"speaker": speaker, "text": text, "completed": completed}


def _transcript(eid, *segs):
    return (eid, {"payload": json.dumps({"type": "transcription", "segments": list(segs)})})


def _card_turn(segments):
    yield {"type": "message-delta", "text": f"saw {len(segments)} lines"}
    yield {"type": "card", "card": {"kind": "person", "title": "Priya", "body": "new attendee"}}


def test_serve_meeting_emits_card_on_new_speaker_gate():
    s = MeetingStream(inbox=[_transcript("1-0", _seg("Jane", "hi"), _seg("Raj", "hello"))])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="unit:u:out", card_turn=_card_turn, idle_ms=10)
    evs = s.events()
    assert any(e["type"] == "card" and e["card"]["title"] == "Priya" for e in evs)
    assert any(e["type"] == "turn-complete" for e in evs)
    assert all(t == "unit:u:out" for t, _ in s.out)


def test_serve_meeting_reaps_on_session_end():
    s = MeetingStream(inbox=[
        _transcript("1-0", _seg("Jane", "a"), _seg("Jane", "b")),
        ("2-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn, idle_ms=10)
    # the session_end flush emitted the buffered beat, then returned
    assert any(e["type"] == "card" for e in s.events())


def test_session_end_runs_write_turn_with_deduped_cards():
    """On session_end, serve_meeting hands the running, de-duped card list to doc_turn (the post-meeting
    WRITE turn) and emits its events tagged `meeting-doc`."""
    captured: dict = {}

    def card_turn(segments):
        # surface the SAME person twice across beats — the doc turn must see it once
        yield {"type": "card", "card": {"kind": "person", "title": "Priya", "body": "lead"}}
        yield {"type": "card", "card": {"kind": "action", "title": "Send quote", "body": "by Fri"}}

    def doc_turn(cards):
        captured["cards"] = cards
        yield {"type": "done", "ok": True, "reply": "wrote kg/entities/meeting/abc.md"}

    s = MeetingStream(inbox=[
        _transcript("1-0", _seg("Jane", "a"), _seg("Raj", "b")),  # new-speaker gate → beat1
        _transcript("2-0", _seg("Jane", "c"), _seg("Jane", "d")),  # buffered, flushed on end
        ("3-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=card_turn,
                  idle_ms=10, doc_turn=doc_turn)

    titles = sorted(c["title"] for c in captured["cards"])
    assert titles == ["Priya", "Send quote"]  # de-duped across beats
    assert any(e.get("turn_id") == "meeting-doc" and e.get("type") == "done" for e in s.events())


def test_serve_meeting_reprocesses_transcript_window_three_passes():
    observed = []

    def card_turn(segs):
        observed.append([(s["segment_id"], s["rewrite_pass"]) for s in segs])
        return iter(())

    s = StepMeetingStream(inbox=[
        _transcript("1-0", {**_seg("Jane", "first"), "segment_id": "a"}),
        _transcript("2-0", {**_seg("Jane", "second"), "segment_id": "b"}),
        _transcript("3-0", {**_seg("Jane", "third"), "segment_id": "c"}),
        _transcript("4-0", {**_seg("Jane", "fourth"), "segment_id": "d"}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=card_turn,
                  idle_ms=10, beat_segments=1)

    assert observed == [
        [("a", 1)],
        [("a", 2), ("b", 1)],
        [("a", 3), ("b", 2), ("c", 1)],
        [("b", 3), ("c", 2), ("d", 1)],
    ]


def test_serve_meeting_processes_drafts_and_upserts_refinements():
    """Live drafts (``completed:false``) are processed, not dropped — and a refining draft of the same
    ``segment_id`` updates the line IN PLACE, so a later beat sees ONE line carrying the final text,
    never a duplicate."""
    observed = []

    def card_turn(segs):
        observed.append([(s["segment_id"], s["text"]) for s in segs])
        return iter(())

    s = StepMeetingStream(inbox=[
        _transcript("1-0", {**_seg("Jane", "hel", completed=False), "segment_id": "a"}),  # draft → beat1
        _transcript("2-0", {**_seg("Jane", "hello world", completed=True), "segment_id": "a"}),  # refine a
        _transcript("3-0", {**_seg("Raj", "hi", completed=True), "segment_id": "b"}),  # new speaker → beat2
    ])
    # beat_segments=5 so beats gate only on a NEW speaker (never the refining draft, which adds no length).
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=card_turn,
                  idle_ms=10, beat_segments=5)

    assert observed[0] == [("a", "hel")]                          # the draft WAS processed, not skipped
    assert observed[-1] == [("a", "hello world"), ("b", "hi")]    # refinement upserted: one "a", final text


def test_serve_meeting_starts_from_injected_cursor():
    s = CursorMeetingStream(inbox=[_transcript("43-0", _seg("Jane", "new"))])
    serve_meeting(
        s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn,
        idle_ms=10, start_id="42-0",
    )
    assert s.cursors[0] == {"tc:m1": "42-0"}


class ProcMeetingStream(MeetingStream):
    """A MeetingStream that also serves as a key/value store (``set``) so we can observe the per-meeting
    processed CURSOR, and exposes the cleaned notes XADD'd to ``proc:meeting:{native}`` separately."""

    def __init__(self, inbox):
        super().__init__(inbox)
        self.kv = {}

    def set(self, key, value):
        self.kv[key] = value

    def proc_notes(self, proc_stream):
        return [json.loads(f["note"]) for name, f in self.out if name == proc_stream and "note" in f]


def _notes_card_turn(segments):
    """A card_turn that returns an LLM-style UPGRADE note for each input segment (id == segment_id)."""
    for seg in segments:
        yield {"type": "note", "note": {"id": seg["segment_id"], "speaker": seg.get("speaker", "Speaker"),
                                        "text": f"clean:{seg.get('text', '')}", "pass": 1, "frozen": False}}


def test_serve_meeting_emits_one_proc_note_per_segment_keyed_by_segment_id():
    """Phase C (a): each NEW segment yields exactly ONE cleaned note onto the SEPARATE processed STREAM
    (``proc:meeting:{native}``), keyed id == segment_id — the reliable 1:1 cleaned channel."""
    s = ProcMeetingStream(inbox=[
        _transcript("1-0", {**_seg("Jane", "um hello there"), "segment_id": "a"}),
        _transcript("2-0", {**_seg("Raj", "yeah hi"), "segment_id": "b"}),
    ])
    serve_meeting(
        s, transcript_stream="tc:m1", out_topic="unit:u:out", card_turn=_card_turn,
        idle_ms=10, proc_stream="proc:meeting:m1", cursor_key="proc:meeting:m1:cursor",
    )
    notes = s.proc_notes("proc:meeting:m1")
    ids = [n["id"] for n in notes]
    # 1:1 — one baseline cleaned note per ingested segment, id == segment_id, never on out_topic.
    assert ids.count("a") >= 1 and ids.count("b") >= 1
    assert {n["id"] for n in notes} == {"a", "b"}
    assert all(n["text"] for n in notes)
    # the cards beat stays on its OWN topic — proc notes are NOT mixed into out_topic
    assert all(name != "proc:meeting:m1" for name, _ in s.out if name == "unit:u:out")


def test_serve_meeting_persists_and_advances_cursor():
    """Phase C (b): the per-meeting CURSOR is persisted and ADVANCES to the last raw stream-id cleaned."""
    s = ProcMeetingStream(inbox=[
        _transcript("5-0", {**_seg("Jane", "first"), "segment_id": "a"}),
        _transcript("6-0", {**_seg("Raj", "second"), "segment_id": "b"}),
    ])
    serve_meeting(
        s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn,
        idle_ms=10, proc_stream="proc:meeting:m1", cursor_key="proc:meeting:m1:cursor",
    )
    assert s.kv["proc:meeting:m1:cursor"] == "6-0"  # advanced to the last cleaned raw entry


def test_serve_meeting_upgrades_proc_note_text_from_llm_rewrite():
    """A valid LLM note for a segment UPGRADES its cleaned text on the proc stream (still 1:1 by id)."""
    s = ProcMeetingStream(inbox=[
        _transcript("1-0", {**_seg("Jane", "raw text"), "segment_id": "a"}, {**_seg("Raj", "more"), "segment_id": "b"}),
    ])
    serve_meeting(
        s, transcript_stream="tc:m1", out_topic="o", card_turn=_notes_card_turn,
        idle_ms=10, beat_segments=1, proc_stream="proc:meeting:m1", cursor_key="proc:meeting:m1:cursor",
    )
    notes = s.proc_notes("proc:meeting:m1")
    by_id = {}
    for n in notes:
        by_id.setdefault(n["id"], []).append(n["text"])
    assert "clean:raw text" in by_id["a"]  # the LLM upgrade landed on the proc stream for id 'a'


# ── Auth-B/#3a: per-meeting workspace file is upserted from proc notes (idempotent) ────────────────

from agent_api.worker import upsert_meeting_transcript_file  # noqa: E402

_MEETING_META = {
    "type": "meeting", "id": "m1", "title": "Meeting m1", "meeting_id": "m1",
    "session_uid": "s1", "platform": "google_meet", "date": "2026-06-27",
}


def test_upsert_meeting_file_writes_then_updates_idempotently(tmp_path):
    """B) Proc notes write/update kg/entities/meeting/<native>.md — a refining note for the same id
    updates the line IN PLACE (no duplicate); a new id appends."""
    path = tmp_path / "kg" / "entities" / "meeting" / "m1.md"
    upsert_meeting_transcript_file(path, _MEETING_META, {"id": "a", "speaker": "Jane", "text": "hello there"})
    upsert_meeting_transcript_file(path, _MEETING_META, {"id": "b", "speaker": "Raj", "text": "hi back"})
    text = path.read_text()
    # frontmatter for the chat agent to identify the meeting
    assert "type: meeting" in text and "id: m1" in text and "title: Meeting m1" in text
    assert "## Speakers" in text and "- Jane" in text and "- Raj" in text
    assert "hello there" in text and "hi back" in text

    # a refining pass for id 'a' UPDATES in place — not duplicated
    upsert_meeting_transcript_file(path, _MEETING_META, {"id": "a", "speaker": "Jane", "text": "hello there, everyone"})
    text2 = path.read_text()
    assert text2.count("<!-- id:a -->") == 1  # idempotent: one line for id 'a'
    assert "hello there, everyone" in text2
    assert text2.count("<!-- id:b -->") == 1

    # re-applying the SAME note is a no-op on content (byte-identical)
    before = path.read_text()
    upsert_meeting_transcript_file(path, _MEETING_META, {"id": "a", "speaker": "Jane", "text": "hello there, everyone"})
    assert path.read_text() == before


def test_serve_meeting_upserts_workspace_file_from_proc_notes(tmp_path):
    """serve_meeting drives on_proc_note → the per-meeting file accumulates one line per segment id."""
    path = tmp_path / "kg" / "entities" / "meeting" / "m1.md"
    s = ProcMeetingStream(inbox=[
        _transcript("1-0", {**_seg("Jane", "um hello"), "segment_id": "a"}),
        _transcript("2-0", {**_seg("Raj", "yeah hi"), "segment_id": "b"}),
    ])
    serve_meeting(
        s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn, idle_ms=10,
        proc_stream="proc:meeting:m1", cursor_key="proc:meeting:m1:cursor",
        on_proc_note=lambda note: upsert_meeting_transcript_file(path, _MEETING_META, note),
    )
    text = path.read_text()
    assert text.count("<!-- id:a -->") == 1 and text.count("<!-- id:b -->") == 1
    assert "Jane" in text and "Raj" in text


def test_run_turn_persists_namespaced_session_file(tmp_path):
    """A chat turn on thread `work` captures the claude session id into its OWN namespaced continuity
    file (`.claude/sessions/work.session`) — distinct threads don't collide on `.claude/.session`."""
    import unittest.mock as mock

    from agent_api import worker

    def fake_exec(argv, cwd):
        yield json.dumps({"type": "result", "subtype": "success", "result": "ok", "session_id": "WORK_SID"})

    with mock.patch.object(worker, "_exec_claude", fake_exec):
        list(worker.run_turn_over_workspace(tmp_path, "hello", session="work"))

    f = tmp_path / ".claude" / "sessions" / "work.session"
    assert f.read_text() == "WORK_SID"
    assert not (tmp_path / ".claude" / ".session").exists()  # never touched the legacy single-thread file


def test_run_turn_starts_fresh_when_resume_transcript_is_too_large(tmp_path, monkeypatch):
    """A bloated Claude Code transcript is preserved on disk, but no longer resumed for a live chat turn."""
    import unittest.mock as mock

    from agent_api import worker

    sess = tmp_path / ".claude" / "sessions"
    sess.mkdir(parents=True)
    (sess / "work.session").write_text("OLD_SID")
    transcript = tmp_path / ".claude" / "projects" / "-workspace"
    transcript.mkdir(parents=True)
    (transcript / "OLD_SID.jsonl").write_text("x" * 32)
    monkeypatch.setenv("VEXA_CHAT_RESUME_MAX_BYTES", "8")
    seen: dict = {}

    def fake_exec(argv, cwd):
        seen["argv"] = argv
        yield json.dumps({"type": "result", "subtype": "success", "result": "ok", "session_id": "NEW_SID"})

    with mock.patch.object(worker, "_exec_claude", fake_exec):
        list(worker.run_turn_over_workspace(tmp_path, "hello", session="work"))

    assert "--resume" not in seen["argv"]
    assert (sess / "work.session").read_text() == "NEW_SID"
    assert (transcript / "OLD_SID.jsonl").exists()


def test_meeting_doc_turn_authors_entity_with_frontmatter_and_links(tmp_path):
    """The real post-meeting WRITE turn: a fake `claude` writes the entity; assert run_unit_turn drove a
    write to kg/entities/meeting/<native>.md with the meeting frontmatter + grouped wikilinks, and the
    prompt carried the surfaced cards."""
    from agent_api import worker

    native = "nba-agyz-gbe"
    seen_prompt: dict = {}

    def fake_exec(argv, cwd):
        # argv is `claude -p <prompt> --output-format ...` — the prompt follows the `-p` flag.
        seen_prompt["prompt"] = argv[argv.index("-p") + 1]
        seen_prompt["tools"] = argv
        # Author the entity exactly where the prompt demands (idempotent target path).
        doc = pathlib.Path(cwd) / "kg" / "entities" / "meeting" / f"{native}.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "---\n"
            "type: meeting\n"
            f"id: {native}\n"
            "title: Meeting nba-agyz-gbe\n"
            f"meeting_id: {native}\n"
            f"session_uid: {native}\n"
            "platform: google_meet\n"
            "date: 2026-06-25\n"
            "---\n\n"
            "Priya joined and committed to sending a quote.\n\n"
            "## Attendees\n- [[Priya]]\n\n## Actions\n- [[Send quote]]\n"
        )
        # minimal claude --output-format stream-json line so run_unit_turn yields a `done`.
        yield json.dumps({"type": "result", "subtype": "success", "result": "wrote", "session_id": "s1"})

    cards = [
        {"kind": "person", "title": "Priya", "body": "lead"},
        {"kind": "action", "title": "Send quote", "body": "by Fri"},
    ]
    # meeting_doc_turn drives the module-level _exec_claude via run_turn_over_workspace; patch it.
    import unittest.mock as mock
    with mock.patch.object(worker, "_exec_claude", fake_exec):
        evs = list(worker.meeting_doc_turn(
            tmp_path, cards, native=native, meeting_id=native, session_uid=native,
            platform="google_meet", date="2026-06-25", title="Meeting nba-agyz-gbe",
        ))

    assert any(e.get("type") == "commit" for e in evs)  # the entity write was committed (governance passed)
    assert "kg/entities/meeting/{}.md".format(native) in seen_prompt["prompt"]
    assert '"Priya"' in seen_prompt["prompt"] and '"Send quote"' in seen_prompt["prompt"]

    doc = (tmp_path / "kg" / "entities" / "meeting" / f"{native}.md").read_text()
    assert "type: meeting" in doc and f"id: {native}" in doc
    assert "session_uid:" in doc and "platform: google_meet" in doc and "date: 2026-06-25" in doc
    assert "[[Priya]]" in doc and "[[Send quote]]" in doc
    # idempotent: a second run updates the same path, not a duplicate
    with mock.patch.object(worker, "_exec_claude", fake_exec):
        list(worker.meeting_doc_turn(
            tmp_path, cards, native=native, meeting_id=native, session_uid=native,
            platform="google_meet", date="2026-06-25", title="Meeting nba-agyz-gbe",
        ))
    found = list((tmp_path / "kg" / "entities" / "meeting").glob("*.md"))
    assert [p.name for p in found] == [f"{native}.md"]


def test_parse_cards_tolerant():
    assert parse_cards('[{"kind":"person","title":"Priya","body":"x"}]')[0]["title"] == "Priya"
    assert parse_cards('{"notes":[],"cards":[{"kind":"topic","title":"Launch","body":"x"}]}')[0]["title"] == "Launch"
    assert parse_cards('Here are the cards:\n[{"kind":"action","title":"Send quote","body":"by Fri"}] done')[0]["kind"] == "action"
    assert parse_cards("nothing salient") == []
    assert parse_cards("[]") == []
    assert parse_cards(None) == []


def test_parse_notes_from_processed_transcript_reply():
    reply = '{"notes":[{"id":"a","speaker":"Jane","chapter":"Live Digest","text":"I want a cleaner digest."}],"cards":[]}'
    notes = parse_notes(reply, {"a": 3})
    assert notes == [{
        "id": "a",
        "speaker": "Jane",
        "chapter": "Live Digest",
        "text": "I want a cleaner digest.",
        "pass": 3,
        "frozen": True,
    }]


def test_parse_notes_adds_segment_timestamp_when_available():
    reply = '{"notes":[{"id":"a","speaker":"Jane","chapter":"Live Digest","text":"I want a cleaner digest."}],"cards":[]}'
    notes = parse_notes(reply, {"a": 2}, {"a": {"start": 42.5}})

    assert notes[0]["t"] == 42.5
    assert notes[0]["pass"] == 2
    assert notes[0]["frozen"] is False


def test_parse_notes_removes_third_person_speaker_boilerplate():
    reply = json.dumps({
        "notes": [
            {"id": "a", "speaker": "Speaker", "chapter": "Launch", "text": "Speaker announces Anthropic released Claude Tag."},
            {"id": "b", "speaker": "Speaker", "chapter": "Concern", "text": "Speaker expresses fear that Anthropic aims to own knowledge work."},
            {"id": "c", "speaker": "Speaker", "chapter": "Tools", "text": "Speaker mentions using Slack and finding the feature appealing."},
        ],
        "cards": [],
    })
    notes = parse_notes(reply)

    assert [n["text"] for n in notes] == [
        "Anthropic released Claude Tag.",
        "I'm afraid Anthropic aims to own knowledge work.",
        "I use Slack and find the feature appealing.",
    ]


def test_meeting_card_turn_parses_streamed_delta_when_done_reply_is_empty(tmp_path):
    import unittest.mock as mock
    from agent_api import worker

    def fake_run(*_args, **_kwargs):
        yield {"type": "message-delta", "text": '{"notes":[{"id":"a","speaker":"Jane","chapter":"Plan","text":"I will send the plan."}],"cards":['}
        yield {"type": "message-delta", "text": '{"kind":"company","title":"Acme","body":"Customer mentioned."}]}'}
        yield {"type": "done", "ok": True, "reply": ""}

    with mock.patch.object(worker, "run_turn_over_workspace", fake_run):
        evs = list(meeting_card_turn(tmp_path, [{"segment_id": "a", "speaker": "Jane", "text": "I'll send the plan.", "start": 7.0}], model="openrouter/free"))

    assert evs[0] == {
        "type": "note",
        "note": {
            "id": "a",
            "speaker": "Jane",
            "chapter": "Plan",
            "text": "I will send the plan.",
            "pass": 1,
            "frozen": False,
            "t": 7.0,
        },
    }
    assert evs[1]["type"] == "card"
    assert evs[1]["card"]["title"] == "Acme"


def test_meeting_card_turn_falls_back_when_model_omits_matching_notes(tmp_path):
    import unittest.mock as mock
    from agent_api import worker

    def fake_run(*_args, **_kwargs):
        yield {
            "type": "done",
            "ok": True,
            "reply": '{"notes":[{"id":"1","speaker":"Jane","chapter":"Plan","text":"Speaker says I will send the plan."}],"cards":[]}',
        }

    with mock.patch.object(worker, "run_turn_over_workspace", fake_run):
        evs = list(meeting_card_turn(
            tmp_path,
            [{"segment_id": "seg-a", "speaker": "Jane", "text": "Speaker says I will send the plan.", "start": 7.0, "rewrite_pass": 2}],
            model="openrouter/free",
        ))

    assert evs[0] == {
        "type": "model-error",
        "error": {
            "stage": "meeting-card",
            "model": "openrouter/free",
            "message": "model response did not include processed transcript notes",
        },
    }
    assert evs[1] == {
        "type": "note",
        "note": {
            "id": "seg-a",
            "speaker": "Jane",
            "chapter": "Live Transcript",
            "text": "I will send the plan.",
            "pass": 2,
            "frozen": False,
            "t": 7.0,
        },
    }


def test_meeting_card_turn_surfaces_model_done_failure(tmp_path):
    import unittest.mock as mock
    from agent_api import worker

    def fake_run(*_args, **_kwargs):
        yield {"type": "done", "ok": False, "reply": "model unavailable"}

    with mock.patch.object(worker, "run_turn_over_workspace", fake_run):
        evs = list(meeting_card_turn(tmp_path, [_seg("Jane", "hello")], model="openrouter/free"))

    assert evs == [{
        "type": "model-error",
        "error": {"stage": "meeting-card", "model": "openrouter/free", "message": "model unavailable"},
    }]


def test_meeting_card_turn_surfaces_model_exception(tmp_path):
    import unittest.mock as mock
    from agent_api import worker

    def fake_run(*_args, **_kwargs):
        raise RuntimeError("router rejected request")
        yield {}

    with mock.patch.object(worker, "run_turn_over_workspace", fake_run):
        evs = list(meeting_card_turn(tmp_path, [_seg("Jane", "hello")], model="openrouter/free"))

    assert evs == [{
        "type": "model-error",
        "error": {"stage": "meeting-card", "model": "openrouter/free", "message": "router rejected request"},
    }]


# ── workspace-driven config knobs wired through serve_meeting / the prompt ─────────────────────────

from agent_api.worker import build_card_prompt  # noqa: E402


def test_parse_cards_filters_to_allowed_kinds():
    reply = '[{"kind":"person","title":"P","body":""},{"kind":"topic","title":"T","body":""}]'
    out = parse_cards(reply, card_kinds=["person"])
    assert [c["title"] for c in out] == ["P"]  # topic filtered out


def test_build_card_prompt_includes_steering_only_when_present():
    base = build_card_prompt("[Jane] hi", ["person", "action"], steering="")
    assert "Standing instructions from this workspace" not in base
    assert "person, action" in base  # wanted kinds named in the prompt
    steered = build_card_prompt("[Jane] hi", ["person"], steering="Ignore small talk.")
    assert "## Standing instructions from this workspace" in steered
    assert "Ignore small talk." in steered


def test_build_card_prompt_composes_governed_polish_and_tag_rules():
    """A) The prompt is COMPOSED from the workspace-governed polish/tag policy — changing a field
    changes the prompt (prompt-only governance)."""
    p = build_card_prompt("[Jane] hi", ["person"], polish_rules="POLISH-X", tag_rules="TAG-Y")
    assert "## Polish rules (governed by this workspace)" in p
    assert "POLISH-X" in p
    assert "## Tag rules (governed by this workspace)" in p
    assert "TAG-Y" in p
    # changing a field changes the prompt
    p2 = build_card_prompt("[Jane] hi", ["person"], polish_rules="POLISH-DIFFERENT", tag_rules="TAG-Y")
    assert p2 != p
    assert "POLISH-DIFFERENT" in p2 and "POLISH-X" not in p2


def test_build_card_prompt_uses_config_from_meeting_md(tmp_path):
    """The fields flow from agents/meeting.md → MeetingConfig → build_card_prompt end to end."""
    from agent_api.agent_config import load_meeting_config

    md = tmp_path / "agents" / "meeting.md"
    md.parent.mkdir(parents=True)
    md.write_text("---\npolish_rules: ws-polish\ntag_rules: ws-tags\n---\n")
    cfg = load_meeting_config(tmp_path)
    p = build_card_prompt("[Jane] hi", cfg.card_kinds, cfg.steering,
                          polish_rules=cfg.polish_rules, tag_rules=cfg.tag_rules)
    assert "ws-polish" in p and "ws-tags" in p


def test_serve_meeting_cadence_segments_gates_beats():
    """With cadence_segments=2, two completed segments from the SAME speaker trigger a beat (no
    new-speaker gate)."""
    beats = []

    def card_turn(segs):
        beats.append(len(segs))
        return iter(())

    s = MeetingStream(inbox=[_transcript("1-0", _seg("Jane", "a"), _seg("Jane", "b"))])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=card_turn,
                  idle_ms=10, beat_segments=2)
    assert beats == [2]  # the 2-segment buffer fired exactly one beat


def test_serve_meeting_disabled_runs_no_beats_but_still_writes_doc():
    """enabled=false skips all live card beats, but write_meeting_doc (doc_turn) is still honored on
    session_end — the two are independent gates."""
    beats = []
    doc_ran = []

    def card_turn(segs):
        beats.append(segs)
        return iter(())

    def doc_turn(cards):
        doc_ran.append(cards)
        yield {"type": "done", "ok": True}

    s = MeetingStream(inbox=[
        _transcript("1-0", _seg("Jane", "a"), _seg("Raj", "b")),  # would gate on new speaker
        ("2-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=card_turn,
                  idle_ms=10, doc_turn=doc_turn, enabled=False)
    assert beats == []          # no live beats ran
    assert doc_ran == [[]]      # doc turn still ran (with no accumulated cards)


def test_serve_meeting_doc_gating_off_skips_doc_turn():
    """When write_meeting_doc=false, main passes doc_turn=None — session_end reaps with no write."""
    s = MeetingStream(inbox=[
        _transcript("1-0", _seg("Jane", "a"), _seg("Raj", "b")),
        ("2-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn,
                  idle_ms=10, doc_turn=None)
    # cards still emitted live, but no meeting-doc turn events
    assert not any(e.get("turn_id") == "meeting-doc" for e in s.events())


# ── workspace skills: governed skills/ symlinked into .claude/skills ──────────────────────────────

def test_link_skills_creates_dir_and_symlink(tmp_path):
    """Creates skills/ and points .claude/skills at it."""
    _link_skills_into_workspace(tmp_path)
    skills = tmp_path / "skills"
    link = tmp_path / ".claude" / "skills"
    assert skills.is_dir()
    assert link.is_symlink()
    assert pathlib.Path(link.readlink()) == skills


def test_link_skills_idempotent(tmp_path):
    """Running twice leaves a single correct symlink; an existing skill file survives."""
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text("x")
    _link_skills_into_workspace(tmp_path)
    _link_skills_into_workspace(tmp_path)
    link = tmp_path / ".claude" / "skills"
    assert link.is_symlink()
    assert (link / "demo" / "SKILL.md").read_text() == "x"


def test_link_skills_does_not_clobber_real_skills_dir(tmp_path):
    """A pre-existing real skills/ dir + its files are preserved, not replaced."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "keep.md").write_text("keep")
    _link_skills_into_workspace(tmp_path)
    assert (tmp_path / "skills" / "keep.md").read_text() == "keep"


def test_link_skills_corrects_wrong_existing_symlink(tmp_path):
    """A stale .claude/skills symlink pointing elsewhere is repointed at skills/."""
    wrong = tmp_path / "elsewhere"
    wrong.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "skills").symlink_to(wrong, target_is_directory=True)
    _link_skills_into_workspace(tmp_path)
    link = claude / "skills"
    assert link.is_symlink()
    assert pathlib.Path(link.readlink()) == tmp_path / "skills"


def test_link_skills_keeps_real_claude_skills_dir(tmp_path):
    """If .claude/skills is a real dir (not a symlink), leave it untouched."""
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "x.md").write_text("real")
    _link_skills_into_workspace(tmp_path)
    link = tmp_path / ".claude" / "skills"
    assert not link.is_symlink() and link.is_dir()
    assert (link / "x.md").read_text() == "real"


def test_seed_claude_md_defers_copilot_steering_to_meeting_md():
    """Guard: the workspace-seed CLAUDE.md must declare agents/meeting.md as the EXCLUSIVE source of
    meeting-copilot steering and must not itself carry copilot behavior. CLAUDE.md is auto-loaded as
    project memory on every turn, so copilot steering here would be a second, conflicting source."""
    seed = pathlib.Path(__file__).resolve().parents[1] / "workspace-seed" / "CLAUDE.md"
    text = seed.read_text()
    lower = text.lower()
    # Names meeting.md as the governing source, with an exclusivity word.
    assert "agents/meeting.md" in text
    assert "exclusiv" in lower
    # No copilot watch/ignore steering smuggled into CLAUDE.md (only the guard *mentions* the words).
    assert "surface only new entities" not in lower
    assert "real-time meeting behavior" not in lower
