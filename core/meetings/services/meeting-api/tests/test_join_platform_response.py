"""What the platform returned at a refusal is stored with the join evidence (D367, card
brain:card/9095fe9ee7c0): one shape for every platform; a room-rule refusal and an automated-check refusal
stay distinguishable; no free text from the platform is stored, so no token, email, name or phone number can
be (review of 63478b49, F1-F3: allowlist, not a redactor)."""
import json

from meeting_api.lifecycle.join_evidence import (
    clean_platform_response, evidence_from_event, normalize_evidence, platform_codes,
)

ROOM_RULE = {"call": "ResolveMeetingSpace", "status": 403,
             "body": '[[7,"The caller does not have permission",[["type.googleapis.com/google.rpc.ErrorInfo",["MEETING_SPACE_RESTRICTED"]]]]]',
             "page_text": "You can't join this video call\nNo one can join a meeting unless invited or admitted by the host"}
AUTO_CHECK = {"call": "CreateMeetingDevice", "status": 403,
              "body": '[[7,"Permission denied",[["type.googleapis.com/google.rpc.ErrorInfo",["ABUSE_DETECTED"]]]]]',
              "page_text": "You can't join this video call"}


def bot_event(answer):
    return {"status": "failed", "completion_reason": "platform_join_refused",
            "join_evidence": {"reason": "platform_join_refused", "source": "bot", "stage": "joining",
                              "detail": "google_meet_join_refused: x", "platform_response": answer}}


def stored(answer):
    return evidence_from_event(bot_event(answer), stage="joining")["platform_response"]


def test_the_platform_answer_rides_the_stored_evidence_as_codes_and_known_text():
    pr = stored(ROOM_RULE)
    assert pr["call"] == "ResolveMeetingSpace" and pr["status"] == 403
    assert pr["codes"] == [7, "ErrorInfo"]   # an unlisted reason word is never stored; the digest tells answers apart
    assert pr["page_text"] == ROOM_RULE["page_text"] and len(pr["body_digest"]) == 16


def test_two_refusals_with_the_same_403_stay_apart_by_what_the_platform_said():
    a, b = stored(ROOM_RULE), stored(AUTO_CHECK)
    assert a["status"] == b["status"] == 403
    assert "MEETING_SPACE_RESTRICTED" not in json.dumps(a) and "ABUSE_DETECTED" not in json.dumps(b)
    assert a["body_digest"] != b["body_digest"]


def test_the_same_shape_holds_for_zoom_and_teams():
    z = normalize_evidence({"reason": "platform_join_refused", "source": "bot", "platform_response":
                            {"call": "join", "status": 200, "body": '{"errorCode":3707,"errorMessage":"MEETING_LOCKED"}',
                             "page_text": "This meeting has been locked by the host"}})["platform_response"]
    assert z["codes"] == [3707] and "page_text" not in z and len(z["page_text_digest"]) == 16
    t = normalize_evidence({"reason": "platform_join_refused", "source": "bot", "platform_response":
                            {"page_text": "Sorry, but you were denied access to the meeting."}})["platform_response"]
    assert set(t) == {"page_text_digest"}


LEAKS = [
    '{"token":"abcdefghijklmnopqrstuvwxyz"}', 'access_token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"',
    "SID=abcdefabcdefabcdefabcdef", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig",
    "dmitry%40vexa.ai", "john.doe [at] gmail.com", "ann.smith@example.com", "Jane Smith wants to join",
    "+351 912 345 678", "https://meet.google.com/abc-defg-hij?authuser=x&tk=QWERTYUIOPASDFGHJKLZXCVB",
    "zak=9f8e7d6c5b4a39281706f5e4d3c2b1a0", "ya29.a0AfH6SMBxyz",
]


def test_no_free_text_from_the_platform_is_ever_stored():
    for leak in LEAKS:
        for field in ("body", "page_text"):
            out = json.dumps(clean_platform_response({"status": 403, field: f"[[7,\"{leak}\"]] " + leak}))
            for part in (leak, leak[:12], "abcdefghijkl", "Jane", "Smith", "dmitry", "vexa", "912", "eyJhbG", "QWERTY"):
                if part in leak:
                    assert part not in out, (field, leak, out)


def test_only_known_fields_are_kept_each_typed():
    out = clean_platform_response({"call": "Resolve Meeting<script>", "status": True, "body": " ",
                                   "page_text": "  ", "headers": {"cookie": "SID=abc"}, "url": "https://x?y"})
    assert out is None
    assert clean_platform_response({"status": 99}) is None and clean_platform_response("403") is None


def test_codes_are_capped_and_timestamps_are_not_mangled_into_codes():
    from meeting_api.lifecycle.join_evidence import KNOWN_PLATFORM_CODES
    assert len(platform_codes(" ".join(sorted(KNOWN_PLATFORM_CODES)))) == 20
    assert platform_codes("at 2026-11-12 03:26:35") == []


def test_no_answer_means_no_field_and_the_rest_of_the_evidence_is_unchanged():
    ev = evidence_from_event(bot_event(None), stage="joining")
    assert "platform_response" not in ev and ev["reason"] == "platform_join_refused"


import pytest


@pytest.fixture(autouse=True)
def _digest_key(monkeypatch):
    monkeypatch.setenv("VEXA_EVIDENCE_DIGEST_KEY", "test-key")


CODE_LEAKS = ['{"token":"AKIA4X7_QWERTY9Z_8HJK2"}', "Bearer AB12_CD34_EF56", '{"name":"JOHN_SMITH"}',
              '["DMITRY_GRANKIN_VEXA_AI"]', '{"phone":[351,912,345,678]}', '{"user":"Ann","mail":"ANN_EXAMPLE_COM"}']


def test_codes_carry_only_known_words_and_keyed_integers():
    for leak in CODE_LEAKS:
        assert platform_codes(leak) == [], (leak, platform_codes(leak))
    assert platform_codes('[[7,"x",[["type.googleapis.com/google.rpc.ErrorInfo",["PERMISSION_DENIED"]]]]]') == [7, "ErrorInfo", "PERMISSION_DENIED"]
    assert platform_codes('{"errorCode":3707,"phone":[351,912]}') == [3707]


def test_no_key_no_digest(monkeypatch):
    monkeypatch.delenv("VEXA_EVIDENCE_DIGEST_KEY")
    out = clean_platform_response({"status": 403, "body": '[[7,"x"]]', "page_text": "Jane Smith"})
    assert out == {"status": 403, "codes": [7]}


def test_1c_refusal_position_and_button_are_kept_as_fixed_keys_only():
    """D367 1c, fixtures from doc:meet-joining-what-happens section 0."""
    open_room_bbb = clean_platform_response({"call": "ResolveMeetingSpace", "status": 403, "refused_at": "before_cta", "cta": "none",
                                             "body": '[7,"The caller does not have permission"]'})
    assert open_room_bbb == {"call": "ResolveMeetingSpace", "status": 403, "codes": [7], "refused_at": "before_cta", "cta": "none",
                             "body_digest": open_room_bbb["body_digest"]}
    knock_refused = clean_platform_response({"call": "CreateMeetingDevice", "status": 403, "refused_at": "after_cta", "cta": "ask_to_join"})
    assert knock_refused["refused_at"] == "after_cta" and knock_refused["cta"] == "ask_to_join"
    junk = clean_platform_response({"status": 403, "refused_at": "later", "cta": "Ask to join (Jane)"})
    assert junk == {"status": 403}
