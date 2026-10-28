"""THE SITUATION, BEFORE THE TURN STARTS (founder, app.dev 2026-10-16: "need more awareness").

A call-woken turn answered "I can't see which platform this call is on" and "tell me after the
call". Its brief now carries the whole picture, assembled cheaply (no model call) before the turn:
the call, the room and its workspace (by TITLE, never the slug), the last minutes of the call and the
room, what Vexa already said and noted, and what it may do here — inside a token budget, the most
recent first."""
from control_plane import signal_situation as sit

NOW = 1_800_000_000.0


def _brief(**over):
    kw = dict(
        now=NOW,
        call={"platform": "vexa_call", "title": "Call with dmitry", "started": NOW - 754,
              "participants": ["Dmitry", "Valeriia"], "speaking": "Dmitry",
              "join": "https://app.vexa.ai/call/abc", "recording": "on", "transcript": "live"},
        room={"workspace": "AI Tinkerers Lisbon", "members": ["Dmitry", "Valeriia"],
              "readme": "# AI Tinkerers Lisbon\nMonthly meetup of builders in Lisbon.\n\n## Venue\nlong",
              "open_items": ["Book the venue for November"], "routines": ["Weekly digest (Mon 9:00)"]},
        transcript=[(NOW - 200, "Valeriia", "We agreed on the 3rd."), (NOW - 20, "Dmitry", "Vexa, add Ana to this room")],
        messages=[(NOW - 100, "Valeriia", "link to the deck?")],
        vexa=[(NOW - 300, "Noted the date.")],
        note={"decisions": "- Meetup on the 3rd", "commitments": "", "questions": "- Venue?"},
        rights=sit.room_rights("AI Tinkerers Lisbon"),
    )
    kw.update(over)
    return sit.compose(**kw)


def test_the_brief_names_the_call_the_room_and_the_rights():
    b = _brief()
    assert "a Vexa call" in b and "Minutes" not in b
    assert "Call with dmitry" in b and "12 min" in b and "Dmitry is speaking" in b
    assert "https://app.vexa.ai/call/abc" in b and "Dmitry, Valeriia" in b
    assert "AI Tinkerers Lisbon" in b and "Monthly meetup of builders in Lisbon." in b
    assert "## Venue" not in b                                           # the README's first section only
    assert "Book the venue for November" in b and "Weekly digest" in b
    assert "- Meetup on the 3rd" in b and "- Venue?" in b and "Noted the date." in b
    assert "write AI Tinkerers Lisbon" in b and "Add <email> to this room?" in b
    assert "after the call" in b                                          # …as what it must never say


def test_the_conversation_is_most_recent_first():
    b = _brief()
    assert b.index("Vexa, add Ana to this room") < b.index("We agreed on the 3rd.")


def test_the_brief_stays_inside_its_budget_dropping_the_oldest_talk_first():
    many = [(NOW - 290 + i, "A", f"line {i} " + "x" * 200) for i in range(200)]
    b = _brief(transcript=many)
    assert len(b) <= sit.BUDGET_CHARS
    assert "line 199 " in b and "line 0 " not in b
    assert "## The call" in b and "## What you may do here" in b


def test_a_private_chat_is_the_persons_own():
    b = _brief(room=None, rights=sit.private_rights())
    assert "## The room" not in b and "your person's own chat" in b



def test_adding_a_person_is_an_ask_card_the_member_presses():
    rights = "\n".join(sit.room_rights("AI Tinkerers Lisbon"))
    assert "```ask" in rights and '"primary"' in rights and '"Add"' in rights and '"Cancel"' in rights
    assert "[answer " in rights and "chat_participant_add" in rights


def test_the_workspace_status_is_its_routines_and_what_needs_someone():
    routines = [
        {"id": "flow:digest", "name": "Weekly digest", "status": "active", "where": "ai-tink",
         "last_run": {"status": "failed", "reason": "no mail access"}, "next": "Mon 9:00"},
        {"id": "flow:recap", "name": "Call recap", "status": "active", "where": "ai-tink",
         "last_run": {"status": "done"}, "next": "after each call"},
        {"id": "flow:mine", "name": "My inbox", "status": "active", "where": "desk", "last_run": {}},
    ]
    open_items, rows = sit.workspace_status(routines, "ai-tink")
    assert open_items == ["Weekly digest failed: no mail access"]
    assert rows == ["Weekly digest (failed · Mon 9:00)", "Call recap (ok · after each call)"]
