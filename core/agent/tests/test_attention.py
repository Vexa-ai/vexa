"""The attention read model's rules (`control_plane/attention.py`, home R3 / E1, Vexa-ai/vexa#1783).

Pure functions over plain dicts: what "needs you" and "working here" are, how they rank, how they are
capped, and — the core property — that nothing belonging to a workspace the viewer cannot open, or a
chat they can no longer read, or another person's routine, is ever surfaced or named. No I/O.
"""
from __future__ import annotations

import json

from control_plane import attention as A

NOW = 1_800_000_000.0
OPEN = {"personal", "ws-a", "_global"}


def _act_kinds(needs):
    return [n["act"]["kind"] for n in needs]


# ── slugs ──────────────────────────────────────────────────────────────────────────────────────

def test_a_chat_with_no_target_or_the_persons_own_works_on_the_desk():
    for t in ("", None, "u1", "desk", "personal"):
        assert A.home_slug(t, "u1") == "personal"
    assert A.home_slug("ws-a", "u1") == "ws-a"


def test_a_line_is_clipped_to_one_human_line():
    t = A.clip("x" * 500)
    assert len(t) <= A.TEXT_MAX and t.endswith("…")
    assert A.clip("  two\nlines ") == "two lines"


# ── proposals ──────────────────────────────────────────────────────────────────────────────────

def test_a_desk_proposal_is_an_accept_on_the_desk():
    items = [{"id": "abc123", "source": "meeting:9", "act": "Send the recap", "since": "2026-01-01T00:00:00Z",
              "status": "open", "source_label": "Weekly"}]
    [n] = A.desk_proposal_needs(items)
    assert n["id"] == "proposal:abc123" and n["kind"] == "proposal" and n["workspace"] == "personal"
    assert n["text"] == "Send the recap" and isinstance(n["at"], int)
    assert n["act"] == {"label": "Accept", "kind": "accept-proposal", "proposal": "abc123", "source": "desk"}


def test_a_closed_desk_proposal_is_not_a_need():
    assert A.desk_proposal_needs([{"id": "x", "act": "a", "source": "s", "status": "ran"}]) == []


def test_a_post_call_proposal_needs_its_owner_only_and_only_where_they_can_open():
    def p(pid, owner="u1", ws=None, status="open", title="Notes page"):
        return {"proposal": {"id": pid, "owner": owner, "status": status, "workspace": ws, "title": title,
                             "kind": "page", "created": NOW - 60, "session_id": "s-1"},
                "session": "s-1", "target": "ws-a"}
    rows = [p("p-1", ws="ws-a"), p("p-2", owner="u2"), p("p-3", status="accepted"),
            p("p-4", ws="ws-secret", title="Secret page"), p("p-5", ws="u1")]
    needs = A.session_proposal_needs(rows, "u1", OPEN)
    by = {n["id"]: n for n in needs}
    assert set(by) == {"proposal:p-1", "proposal:p-5"}
    assert by["proposal:p-1"]["workspace"] == "ws-a"
    assert by["proposal:p-5"]["workspace"] == "personal"
    assert by["proposal:p-1"]["act"] == {"label": "Review", "kind": "accept-proposal", "proposal": "p-1",
                                         "session": "s-1", "source": "call"}
    assert "Secret page" not in json.dumps(needs)


def test_a_post_call_proposal_without_a_workspace_lives_where_its_chat_works():
    rows = [{"proposal": {"id": "p-1", "owner": "u1", "status": "open", "workspace": None, "title": "",
                          "kind": "commitment", "created": NOW, "session_id": "s-1"},
             "session": "s-1", "target": "ws-gone"}]
    assert A.session_proposal_needs(rows, "u1", OPEN) == []
    rows[0]["target"] = ""
    assert A.session_proposal_needs(rows, "u1", OPEN)[0]["workspace"] == "personal"


# ── mentions ───────────────────────────────────────────────────────────────────────────────────

def _note(nid, chat, read=False, kind="chat_mentioned", text="Ana mentioned you in “Plan”"):
    return {"id": nid, "kind": kind, "object": {"kind": "chat", "id": chat, "name": "Plan"}, "text": text,
            "created": NOW - 10, "read": read}


def test_an_unread_mention_in_a_readable_chat_opens_that_chat():
    chats = {"s-1": {"readable": True, "session": "s-1", "workspace": "ws-a"}}
    [n] = A.mention_needs([_note("n1", "s-1")], chats, OPEN)
    assert n["kind"] == "mention" and n["workspace"] == "ws-a" and n["id"] == "mention:n1"
    assert n["act"] == {"label": "Open", "kind": "open-session", "session": "s-1", "notification": "n1"}


def test_a_mention_from_a_chat_you_can_no_longer_read_is_dropped():
    chats = {"s-1": {"readable": False, "session": "s-1", "workspace": "ws-a"}}
    assert A.mention_needs([_note("n1", "s-1")], chats, OPEN) == []
    assert A.mention_needs([_note("n1", "s-unknown")], {}, OPEN) == []


def test_read_mentions_other_kinds_and_foreign_workspaces_are_not_needs():
    chats = {"s-1": {"readable": True, "session": "s-1", "workspace": "ws-a"},
             "s-2": {"readable": True, "session": "s-2", "workspace": "ws-secret"}}
    notes = [_note("n1", "s-1", read=True), _note("n2", "s-1", kind="chat_added"), _note("n3", "s-2")]
    assert A.mention_needs(notes, chats, OPEN) == []


# ── cards ──────────────────────────────────────────────────────────────────────────────────────

def _card(cid, waiting=None, ws=(), members=(("u1", "member"),), title="Renewal", next_=None):
    return {"id": cid, "title": title, "state": "Proposal", "next": next_, "waiting": waiting, "due": None,
            "workspaces": list(ws), "members": [{"subject": s, "status": st} for s, st in members]}


ME = {"u1", "u1@example.test", "ana kovacs"}


def test_a_card_waiting_on_you_is_a_need_where_the_card_lives():
    [n] = A.card_needs([_card("c1", waiting="Ana Kovacs", ws=["ws-a"])], ME, OPEN)
    assert n["kind"] == "card" and n["workspace"] == "ws-a" and n["id"] == "card:c1"
    assert n["act"] == {"label": "Open", "kind": "open-card", "card": "c1"}
    assert "Renewal" in n["text"]
    [n] = A.card_needs([_card("c2", waiting="u1@example.test")], ME, OPEN)
    assert n["workspace"] == "personal"


def test_a_card_waiting_on_someone_else_or_on_nobody_is_not_a_need():
    assert A.card_needs([_card("c1", waiting="the client", ws=["ws-a"]), _card("c2")], ME, OPEN) == []


def test_a_card_you_are_not_on_is_never_a_need_whatever_it_says():
    assert A.card_needs([_card("c1", waiting="u1", members=(("u2", "member"),))], ME, OPEN) == []


def test_a_card_naming_a_workspace_you_cannot_open_is_dropped():
    cards = [_card("c1", waiting="u1", ws=["ws-secret"], title="Secret deal")]
    assert A.card_needs(cards, ME, OPEN) == []
    assert A.card_home(cards[0], OPEN) is None
    # one of its workspaces is yours: it lives there, the other is never named
    [n] = A.card_needs([_card("c2", waiting="u1", ws=["ws-secret", "ws-a"])], ME, OPEN)
    assert n["workspace"] == "ws-a" and "ws-secret" not in json.dumps(n)


# ── upcoming calls ─────────────────────────────────────────────────────────────────────────────

def _up(mid, start, homes=("ws-a",), note=None, name="Kickoff"):
    return {"meeting": str(mid), "name": name, "start": start, "homes": list(homes), "note": note}


def test_a_call_starting_within_a_day_is_a_need_and_prepare_opens_its_note():
    note = {"slug": "ws-a", "path": "kg/entities/meeting/x.md"}
    [n] = A.upcoming_needs([_up(7, NOW + 3600, note=note)], NOW, OPEN)
    assert n["kind"] == "upcoming" and n["workspace"] == "ws-a" and n["at"] == int(NOW + 3600)
    assert n["act"] == {"label": "Prepare", "kind": "prepare", "path": note["path"], "slug": "ws-a",
                        "meeting": "7"}
    [n] = A.upcoming_needs([_up(8, NOW + 7200, homes=())], NOW, OPEN)
    assert n["workspace"] == "personal" and n["act"]["kind"] == "open-meeting" and n["act"]["meeting"] == "8"


def test_a_call_about_to_start_offers_join():
    [n] = A.upcoming_needs([_up(7, NOW + 120)], NOW, OPEN)
    assert n["act"]["label"] == "Join" and n["act"]["kind"] == "open-meeting"


def test_past_and_far_calls_are_not_upcoming_and_foreign_homes_are_never_named():
    assert A.upcoming_needs([_up(1, NOW - 60), _up(2, NOW + 2 * 86400)], NOW, OPEN) == []
    [n] = A.upcoming_needs([_up(3, NOW + 60, homes=("ws-secret",))], NOW, OPEN)
    assert n["workspace"] == "personal" and "ws-secret" not in json.dumps(n)


# ── routines ───────────────────────────────────────────────────────────────────────────────────

def _routine(name, status="active", last=None, where="ws-a", next_at=None, owner="u1"):
    return {"id": f"flow:{name}", "name": name.capitalize(), "status": status, "last_run": last,
            "where": where, "next_at": next_at, "owner": owner}


def test_a_failed_routine_is_a_retry():
    r = _routine("brief", last={"id": "rx-1", "status": "failed", "reason": "no access",
                                "ended_at": NOW - 300})
    [n] = A.failed_routine_needs([r], "u1", OPEN)
    assert n["kind"] == "failed" and n["workspace"] == "ws-a" and n["id"] == "failed:flow:brief"
    assert n["act"] == {"label": "Retry", "kind": "retry-routine", "routine": "flow:brief", "reaction": "rx-1"}
    assert "no access" in n["text"]


def test_a_paused_ok_or_foreign_routine_is_not_a_need():
    rs = [_routine("a", last={"status": "done"}),
          _routine("b", status="paused", last={"status": "failed"}),
          _routine("c", last={"status": "failed"}, owner="u2"),
          _routine("d", last={"status": "failed"}, where="ws-secret")]
    assert A.failed_routine_needs(rs, "u1", OPEN) == []


def test_routine_work_states():
    assert A.routine_work(_routine("a", last={"status": "running", "started_at": NOW - 5}), NOW)["state"] == "running"
    ok = A.routine_work(_routine("a", last={"status": "done", "ended_at": NOW - 7200}), NOW)
    assert ok["state"] == "ok" and ok["when"] == "2h ago" and ok["ref"] == "agent:flow:a"
    nxt = A.routine_work(_routine("a", next_at=NOW + 1800), NOW)
    assert nxt["state"] == "next" and nxt["when"] == "in 30m" and nxt["at"] == int(NOW + 1800)
    assert A.routine_work(_routine("a", status="paused"), NOW)["state"] == "paused"
    assert A.routine_work(_routine("a", last={"status": "refused"}), NOW)["state"] == "failed"


# ── rooms ──────────────────────────────────────────────────────────────────────────────────────

def test_rooms_active_counts_shared_chats_active_in_the_window():
    chats = [{"id": "s-1", "workspace": "ws-a", "humans": 2, "last_active": NOW - 60},
             {"id": "s-2", "workspace": "ws-a", "humans": 3, "last_active": NOW - A.ROOM_ACTIVE_SEC - 1},
             {"id": "s-3", "workspace": "ws-a", "humans": 1, "last_active": NOW - 60},
             {"id": "s-4", "workspace": "personal", "humans": 2, "last_active": NOW - 10}]
    assert A.rooms_active(chats, NOW) == {"ws-a": 1, "personal": 1}


# ── ranking and composition ────────────────────────────────────────────────────────────────────

def _n(kind, nid, at, ws="personal"):
    return {"id": f"{kind}:{nid}", "kind": kind, "workspace": ws, "text": nid, "at": int(at),
            "act": {"label": "Open", "kind": "open-session"}}


def test_ranking_failed_and_proposals_then_mentions_then_cards_then_upcoming_soonest():
    needs = [_n("upcoming", "late", NOW + 5000), _n("card", "c", NOW - 10), _n("mention", "m", NOW - 50),
             _n("proposal", "p-old", NOW - 500), _n("failed", "f", NOW - 100), _n("upcoming", "soon", NOW + 60),
             _n("proposal", "p-new", NOW - 5)]
    assert [n["text"] for n in A.rank(needs)] == ["p-new", "f", "p-old", "m", "c", "soon", "late"]


def test_compose_keys_are_the_openable_slugs_and_caps_hold():
    needs = [_n("mention", f"m{i}", NOW - i, ws="ws-a") for i in range(40)]
    needs.append(_n("card", "x", NOW, ws="ws-secret"))          # never a key, never in needs
    out = A.compose(now=NOW, slugs=["personal", "ws-a"], needs=needs,
                    working={"ws-a": [{"kind": "routine", "label": f"r{i}", "state": "ok"} for i in range(12)]},
                    rooms={"ws-a": 2, "ws-secret": 5}, live={"ws-a": {"meeting": "1"}},
                    last={"ws-a": "Ana changed Plan", "ws-secret": "secret"})
    assert set(out["workspaces"]) == {"personal", "ws-a"}
    assert out["generated_at"] == int(NOW)
    assert len(out["needs"]) == A.NEEDS_MAX
    w = out["workspaces"]["ws-a"]
    assert len(w["needs"]) == A.WS_NEEDS_MAX and len(w["working"]) == A.WORKING_MAX
    assert w["rooms_active"] == 2 and w["live_call"] == {"meeting": "1"} and w["last"] == "Ana changed Plan"
    p = out["workspaces"]["personal"]
    assert p == {"needs": [], "working": [], "rooms_active": 0, "live_call": None, "last": None}
    blob = json.dumps(out)
    assert "ws-secret" not in blob and "secret" not in blob


def test_compose_dedups_a_need_homed_in_two_workspaces_in_the_global_list():
    a, b = _n("upcoming", "7", NOW + 60, ws="ws-a"), _n("upcoming", "7", NOW + 60, ws="personal")
    out = A.compose(now=NOW, slugs=["personal", "ws-a"], needs=[a, b], working={}, rooms={}, live={}, last={})
    assert len(out["needs"]) == 1
    assert len(out["workspaces"]["ws-a"]["needs"]) == 1 and len(out["workspaces"]["personal"]["needs"]) == 1


def test_compose_for_one_workspace_answers_only_that_key():
    needs = [_n("mention", "m1", NOW, ws="ws-a"), _n("mention", "m2", NOW, ws="personal")]
    out = A.compose(now=NOW, slugs=["personal", "ws-a"], needs=needs, working={}, rooms={}, live={},
                    last={}, only="ws-a")
    assert list(out["workspaces"]) == ["ws-a"] and [n["text"] for n in out["needs"]] == ["m1"]
    empty = A.compose(now=NOW, slugs=["personal", "ws-a"], needs=needs, working={}, rooms={}, live={},
                      last={}, only="ws-secret")
    assert empty["workspaces"] == {} and empty["needs"] == []


# ── refs: the invisible shape ──────────────────────────────────────────────────────────────────

def test_an_invisible_ref_is_exactly_its_kind_and_false():
    assert A.invisible("room") == {"kind": "room", "visible": False}
    assert A.parse_ref("room:s-1") == ("room", "s-1")
    assert A.parse_ref("nonsense") == (None, None)
    assert A.parse_ref("weird:x") == (None, None)
    assert A.parse_ref("chat:") == (None, None)


def test_a_card_ref_is_visible_only_from_the_body_and_an_openable_workspace():
    cards = [_card("c1", waiting="u1", ws=["ws-a"], next_="Send terms"), _card("c2", ws=["ws-secret"], title="Hidden")]
    assert A.card_ref("c1", cards, OPEN) == {"kind": "card", "visible": True, "name": "Renewal", "state": "Proposal",
                                             "sub": "Waiting on u1 · Next: Send terms"}
    assert A.card_ref("c2", cards, OPEN) == {"kind": "card", "visible": False}
    assert A.card_ref("c9", cards, OPEN) == {"kind": "card", "visible": False}


def test_run_history_is_one_routines_runs_newest_first_capped():
    runs = [{"flow": "brief", "status": "done", "started_at": 10, "updated_at": 11},
            {"flow": "other", "status": "failed", "started_at": 30, "updated_at": 31},
            {"flow": "brief", "status": "failed", "reason": "timed out", "started_at": 20, "updated_at": 21},
            {"flow": "brief", "status": "running", "started_at": 40, "updated_at": 40}]
    out = A.run_history(runs, "flow:brief")
    assert [r["at"] for r in out] == [40, 21, 11]
    assert [r["ok"] for r in out] == [True, False, True] and "timed out" in out[1]["what"]
    assert A.run_history(runs * 20, "flow:brief", limit=5).__len__() == 5
    assert A.run_history(None, "flow:brief") == []


def test_the_flows_client_reads_the_persons_own_runs(monkeypatch):
    from control_plane import routine_flows as rf
    seen = {}

    def fake(self, method, path, body=None):
        seen["call"] = (method, path)
        return {"subject": "u1", "runs": [{"flow": "brief", "status": "done"}]}
    monkeypatch.setattr(rf.FlowsClient, "_call", fake)
    out = rf.FlowsClient(base="http://flows", key="k").runs("u1", limit=500)
    assert out == [{"flow": "brief", "status": "done"}]
    assert seen["call"] == ("GET", "/flows/runs?subject=u1&limit=200")


def test_an_identity_proposal_is_a_need_on_that_workspace_for_its_owner_only():
    rows = [{"slug": "kestrel", "name": "Kestrel pilot", "owner": True, "proposal": {"by": "u1", "source": "designed", "at": 50}},
            {"slug": "other", "name": "Other", "owner": False, "proposal": {"by": "u1", "source": "designed", "at": 60}},
            {"slug": "plain", "name": "Plain", "owner": True, "proposal": None},
            {"slug": "foreign", "name": "Foreign", "owner": True, "proposal": {"by": "u1", "at": 70}}]
    out = A.identity_needs(rows, {"kestrel", "other", "plain"})
    assert [n["workspace"] for n in out] == ["kestrel"]
    n = out[0]
    assert n["kind"] == "proposal" and n["act"]["source"] == "identity" and n["act"]["slug"] == "kestrel"
    assert n["act"]["label"] == "Use" and "Kestrel pilot" in n["text"]
