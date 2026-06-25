"""worker harness — the redis serve() loop over a fake stream + injected turn (no docker, no claude).

Proves: the entrypoint turn runs first, each turn's UnitEvents XADD to the output Stream tagged with a
turn id + a turn-complete marker, interactive messages on the input Stream run in order, a `stop`
message exits, and an idle read reaps the harness (returns).
"""
from __future__ import annotations

import json
import pathlib

from agent_api.worker import serve


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

from agent_api.worker import parse_cards, serve_meeting  # noqa: E402


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
    assert parse_cards('Here are the cards:\n[{"kind":"action","title":"Send quote","body":"by Fri"}] done')[0]["kind"] == "action"
    assert parse_cards("nothing salient") == []
    assert parse_cards("[]") == []
    assert parse_cards(None) == []
