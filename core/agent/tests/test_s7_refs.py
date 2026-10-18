"""PRD 0.13.4 D18 (one reference form, one resolver, a reference never grants) and deny test 27.

Every kind goes through `universal_refs.resolve` with the object's own access decision; anchors
(a meeting moment, a transcript line, a page heading) only say where to open. Deny test 27: a pasted
reference to an object the viewer cannot access answers ``not-yours`` (or a state that names
nothing) with no label, and the writer's label is never echoed."""
from __future__ import annotations

import json

import pytest

from control_plane import universal_refs as refs_mod
from workspaces.shared import links

from tests.test_universal_refs import ALICE, DAVE, PAGE, SECRET_TITLE, WS_NAME, _h, _resolve, env  # noqa: F401


def test_meeting_moment_and_line_open_at_the_anchor_for_a_reader(env):
    c = env["c"]
    at = _resolve(c, ALICE, "[[meeting:147@00:12:30]]")
    assert at["access"] == "readable" and at["open"] == {"meeting": 147, "anchor": "t=00:12:30"}
    assert at["url"] == "/r/meeting/147#t=00:12:30" and at["ref"] == "[[meeting:147@00:12:30]]"
    url = _resolve(c, ALICE, "https://app.example.test/r/meeting/147#t=00:12:30")
    assert url["open"] == at["open"]
    line = _resolve(c, ALICE, "[[meeting:147#L42]]")
    assert line["open"] == {"meeting": 147, "anchor": "L42"}
    # the old spelling is the same object
    assert _resolve(c, ALICE, "[[call:147]]")["kind"] == "meeting"


def test_page_heading_is_an_anchor_on_the_page(env):
    c, wid = env["c"], env["wid"]
    got = _resolve(c, ALICE, f"[[page:{wid}/{PAGE}#overview]]")
    assert got["access"] == "readable" and got["open"]["anchor"] == "overview"
    assert got["url"].endswith("#overview")


def test_room_and_message_resolve_through_the_chat_decision(env):
    c, chat = env["c"], env["chat"]
    room = _resolve(c, ALICE, f"[[room:{chat}]]")
    assert room["kind"] == "room" and room["access"] == "readable" and room["label"] == SECRET_TITLE
    msg = _resolve(c, ALICE, f"[[message:{chat}/m-7]]")
    assert msg["access"] == "readable" and msg["open"] == {"chat": chat, "message": "m-7"}
    theirs = _resolve(c, DAVE, f"[[message:{chat}/m-7|{SECRET_TITLE}]]")
    assert theirs["access"] == "not-yours" and theirs["label"] is None
    assert SECRET_TITLE not in json.dumps(theirs)


def test_query_is_each_viewers_own_list(env):
    got = _resolve(env["c"], DAVE, "[[query:cards?state=open]]")
    assert got["access"] == "readable" and got["open"] == {"query": "cards?state=open"}


def test_card_and_person_without_a_crm_answer_are_unavailable(env):
    for ref in ("[[card:c-230|Venue contract]]", "[[routine:weekly|Weekly digest]]"):
        got = _resolve(env["c"], ALICE, ref)
        assert got["access"] == "unavailable" and got["label"] is None
        assert "Venue" not in json.dumps(got) and "Weekly" not in json.dumps(got)


def test_card_seam_answers_only_cards_the_viewer_read():
    seams = refs_mod.Seams(workspace=lambda w: (None, "gone"), page=lambda w, t: {}, chat=lambda c: (None, False),
                           meeting=lambda m: None,
                           card=lambda cid: ({"title": "Venue contract"}, "readable") if cid == "c-1" else (None, "unavailable"))
    assert refs_mod.resolve("[[card:c-1]]", seams)["label"] == "Venue contract"
    other = refs_mod.resolve("[[card:c-2|Secret]]", seams)
    assert other["access"] == "unavailable" and other["label"] is None


def test_file_is_relative_to_the_page_it_sits_in():
    asked = []

    def page(w, t):
        asked.append((w, t))
        return {"access": "readable", "title": "Venue", "path": t, "slug": "team"}

    seams = refs_mod.Seams(workspace=lambda w: (None, "gone"), page=page, chat=lambda c: (None, False),
                           meeting=lambda m: None, page_workspace="team")
    got = refs_mod.resolve("[[file:notes/venue.md]]", seams)
    assert got["access"] == "readable" and asked == [("team", "notes/venue.md")]
    no_page = refs_mod.Seams(workspace=lambda w: (None, "gone"), page=page, chat=lambda c: (None, False),
                             meeting=lambda m: None)
    assert refs_mod.resolve("[[file:notes/venue.md]]", no_page)["access"] == "gone"


# ── deny test 27: a reference never grants, and never echoes ────────────────────────────────────

DENY_27 = [
    "[[workspace:{wid}|" + WS_NAME + "]]",
    "/r/workspace/{wid}",
    "[[chat:{chat}|" + SECRET_TITLE + "]]",
    "[[room:{chat}|" + SECRET_TITLE + "]]",
    "[[message:{chat}/m-1|" + SECRET_TITLE + "]]",
    "[[page:{wid}/" + PAGE + "#overview|mühlemann]]",
    "https://app.example.test/r/page/{wid}/" + PAGE,
    "[[meeting:147@00:12:30|Pricing call]]",
    "[[entity:{wid}/muhlemann-popp-ag|mühlemann]]",
]


@pytest.mark.parametrize("tmpl", DENY_27)
def test_deny_27_a_pasted_reference_never_grants_and_never_echoes(env, tmpl):
    c = env["c"]
    ref = tmpl.format(wid=env["wid"], chat=env["chat"])
    got = _resolve(c, DAVE, ref)
    assert got["access"] in ("not-yours", "unavailable"), got
    assert got["label"] is None and got["open"] is None
    body = json.dumps(got, ensure_ascii=False)
    for secret in (SECRET_TITLE, WS_NAME, "mühlemann", "Pricing call"):
        assert secret not in body, f"{secret!r} leaked through {ref}"
    # resolving it did not make DAVE a reader of anything: the same answer again, and the object
    # itself still refuses him on its own route
    assert _resolve(c, DAVE, ref)["access"] == got["access"]
    assert c.get("/api/chat/read", params={"session": env["chat"]}, headers=_h(DAVE)).status_code in (403, 404, 405, 422)


def test_deny_27_not_yours_is_the_answer_for_a_chat_and_carries_request_access(env):
    got = _resolve(env["c"], DAVE, f"[[chat:{env['chat']}|{SECRET_TITLE}]]")
    assert got["access"] == "not-yours" and got["request_access"] is True and got["label"] is None


def test_chip_round_trip_keeps_the_anchor():
    r = links.parse_universal("[[meeting:147@0:12:30|x]]")
    assert (r.kind, r.id, r.anchor) == ("meeting", "147", "t=00:12:30")
    assert links.ref_chip(r.kind, r.id, anchor=r.anchor) == "[[meeting:147@00:12:30]]"
    q = links.parse_universal("[[query:cards?state=open]]")
    assert q.kind == "query" and q.id == "cards?state=open"


def test_audience_names_who_cannot_open_and_never_grants(env):
    c, chat, store = env["c"], env["chat"], env["store"]
    room = store.create(ALICE, name="team-room", title="Team")
    store.add_participant(room["id"], ALICE, DAVE)
    r = c.post("/api/refs/audience", headers=_h(ALICE), json={"ref": f"[[chat:{chat}]]", "room": room["id"]})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["everyone"] is False and got["cannot"] == ["U_dave"] and got["where"] == "your desk"
    assert SECRET_TITLE not in json.dumps(got)
    # someone who cannot open the object learns nothing from asking
    assert c.post("/api/refs/audience", headers=_h(DAVE), json={"ref": f"[[chat:{chat}]]", "room": room["id"]}).status_code == 404
    # and asking did not grant: Dave still cannot read the chat
    assert _resolve(c, DAVE, f"/r/chat/{chat}")["access"] == "not-yours"
