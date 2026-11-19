"""ONE NAME FOR A MEETING, AND NEVER AN ID (UI decisions 14; founder 2026-10-15: "humans get crazy
with these names"). The agent, the home, the rail's server label and every notification use it."""
from control_plane import display_name as dn
from control_plane.notifications import text_for
from shared import chat_label


def test_title_then_chat_then_people_then_call_with_vexa():
    assert dn.meeting_display_name({"platform": "google_meet", "data": {"title": "AI Tinkerers planning"}}) == "AI Tinkerers planning"
    assert dn.meeting_display_name({"platform": "vexa_call", "title": "vexa_call · call-21ca0ef9214d"}, chat_name="Lisbon sponsors") == "Lisbon sponsors"
    assert dn.meeting_display_name({"platform": "google_meet", "native_meeting_id": "tog-zpha-qim",
                                    "data": {"participants": ["Dmitry", "Valeriia", "Speaker 2"]}}, me=["Dmitry"]) == "Call with Valeriia"
    assert dn.meeting_display_name({"platform": "vexa_call", "data": {"participants": ["Dmtiry Grankin"]}}) == "Call with Vexa"
    assert dn.meeting_display_name({"platform": "google_meet"}) == "Call with Vexa"


def test_nothing_shown_looks_like_an_id():
    for raw in ("vexa_call · call-21ca0ef9214d", "tog-zpha-qim", "pchat-mv747eaw", "92446951537", "Google Meet call"):
        assert dn.looks_raw(raw), raw
    for row in ({"platform": "vexa_call", "title": "vexa_call · call-21ca0ef9214d"},
                {"platform": "zoom", "title": "zoom · 92446951537", "data": {"participants": ["Maja Brlek"]}}):
        assert not dn.looks_raw(dn.meeting_display_name(row))


def test_a_notification_never_quotes_an_id():
    assert text_for("bot_joined", "Vexa", "call-0c90e2a5fcff") == "Vexa joined your call"
    assert text_for("bot_joined", "Vexa", "jfw-jmbe-pjx") == "Vexa joined your meeting"
    assert text_for("bot_joined", "Vexa", "Pricing review") == "Vexa joined “Pricing review”"


def test_a_home_act_tail_is_never_a_chat_name():
    assert chat_label.human_head("Start a call with Vexa. (This came from your home, item t-call. When it is done, call home_item_update …)") \
        == "Start a call with Vexa."


def test_a_starter_with_a_hidden_instruction_is_named_by_the_persons_line():
    kick = ("Create a shared workspace with me. Ask me in one line what it is for. Then call workspace_templates_list, "
            "suggest the template that fits, and create it with workspace_create.\n\n<!-- vexa:say -->Create a shared workspace.")
    assert chat_label.human_head(kick) == "Create a shared workspace."
