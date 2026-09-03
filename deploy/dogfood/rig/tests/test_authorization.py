"""R-D06 · R-D07 · R-D12 · R-D19 · R-D21 — who may do what, and over whose rows."""
from __future__ import annotations

import json

from conftest import as_user, tool
import vexa_control_mcp as rig


def test_rd06_the_autonomous_regime_may_not_speak_or_delete(monkeypatch):
    """GATE 3 (R-D06). Decision 7 said the autonomous client — a model dispatched with no person in
    the loop — does not speak into a live room and does not delete meetings. The regime rode along
    in every delegated token, was PRINTED at two places, and was read for authorization at none;
    `bot_say`'s only guard was `asked_by_a_human`, an argument the calling model sets about itself.
    """
    as_user(monkeypatch, "7")
    rig.CALL_SCOPE.set({"regime": "autonomous", "workspaces": ["team"]})

    for verb, kwargs in (("bot_say", {"meeting_url": "https://meet.google.com/abc-defg-hij",
                                      "text": "hello", "asked_by_a_human": True}),
                         ("meeting_delete", {"meeting_id": "12"})):
        out = json.loads(tool(verb)(**kwargs))
        assert out.get("refused") == "regime", f"{verb} ran under the autonomous regime"

    # …and the human regime is unaffected: the reduction is a ceiling on one dispatch shape, not a
    # new refusal for the person's own agent.
    rig.CALL_SCOPE.set({"regime": "human", "workspaces": "*"})
    out = json.loads(tool("meeting_delete")(meeting_id="12"))
    assert out.get("refused") != "regime"


def test_rd07_reaction_signal_steers_only_the_callers_own_reaction(monkeypatch):
    """GATE 4a (R-D07). `reaction_signal` posts with the lane's admin key and never checked the
    reaction against the caller, while `reactions_list` handed out every id instance-wide — so
    `cancel` on a stranger's scheduled join was one call away for any signed-in user.

    The check is OWNERSHIP, not operator authority, and the difference is the product: a person
    stopping the join THEY scheduled with `bot_schedule` is the ordinary path, so an admin-only
    gate here would close the common case to fix the rare one. The decision is made by the service
    that owns the row — `subject` on the signal route — never here by matching strings.
    """
    # A COLLEAGUE'S reaction: the owning service answers 403 and the verb refuses by name.
    http = as_user(monkeypatch, "7",
                   routes={"/reactions/r-theirs/cancel": (403, "that reaction is not yours")})
    out = json.loads(tool("reaction_signal")("r-theirs", "cancel"))
    assert out.get("refused") == "not_yours", out
    assert "subject=7" in http.urls("/reactions/r-theirs")[0]

    # THEIR OWN: it goes through, and the subject rides along so the service can decide.
    http = as_user(monkeypatch, "7", routes={"/reactions/r-mine/cancel": (200, {"cancel": True})})
    out = json.loads(tool("reaction_signal")("r-mine", "cancel"))
    assert out.get("refused") is None, out
    assert out["result"] == {"cancel": True}
    url = http.urls("/reactions/r-mine/cancel")[0]
    assert "subject=7" in url, url

    # An id that does not exist is its OWN answer — telling somebody "not yours" about a reaction
    # nobody has sends them off to re-derive an id instead of asking for a real one.
    http = as_user(monkeypatch, "7", routes={"/reactions/r-ghost/cancel": (404, "no such reaction")})
    assert json.loads(tool("reaction_signal")("r-ghost", "cancel"))["refused"] == "no_such_reaction"


def test_rd07_reaction_signal_is_not_operator_gated(monkeypatch):
    """The other half of the same ruling, pinned so it cannot drift back: an ordinary,
    non-admin account reaches this verb. It was briefly `_operator_gate`d, which would have made
    `bot_schedule` mint a reaction its own author could not cancel."""
    http = as_user(monkeypatch, "7", admin=False,
                   routes={"/reactions/r-mine/cancel": (200, {"cancel": True})})
    out = json.loads(tool("reaction_signal")("r-mine", "cancel"))
    assert out.get("refused") is None, out
    assert http.urls("/reactions/r-mine/cancel"), "a non-admin never reached the service"


def test_rd07_reactions_list_is_scoped_to_the_caller(monkeypatch):
    """GATE 4b (R-D07). The operator projection, asked for as this person's."""
    http = as_user(monkeypatch, "7")
    tool("reactions_list")(status="failed")
    urls = http.urls("/reactions")
    assert urls and "subject=7" in urls[0], urls
    assert "status=failed" in urls[0]


def test_rd12_whats_waiting_asks_only_for_this_subject(monkeypatch):
    """GATE 8 (R-D12). The tool nobody can avoid calling was fetching `{FLOWS}/reactions` with no
    subject filter and reporting other tenants' flow names, step names and failure reasons as this
    person's queue."""
    http = as_user(monkeypatch, "7")
    tool("whats_waiting")()
    urls = http.urls("/reactions")
    assert urls, "whats_waiting did not read the reaction queue at all"
    assert all("subject=7" in u for u in urls), urls


def test_rd19_whats_waiting_is_anon_guarded(monkeypatch):
    """GATE 11 (R-D19). It was the one account tool with no `@_anon_guard`, so it set CALL_TOKEN by
    hand: it took a credential from a call argument, and it CLEARED a live token whenever the kwarg
    was absent — de-authenticating the first call every agent makes.

    The invariant changed under this test on 2026-09-03 and it changed in one direction only: the
    argument used to be honoured unless `VEXA_RIG_MODE=0` said otherwise, and is now **always
    ignored**, because the second authentication path is gone rather than switched off. What the
    guard must still do is leave a live connection's credential alone.
    """
    as_user(monkeypatch, "7")
    rig.CURRENT.set(None)
    rig.CALL_TOKEN.set("vxa_mcp_LIVE")

    tool("whats_waiting")()                    # the connection's credential, no argument
    assert rig.CALL_TOKEN.get() == "vxa_mcp_LIVE", "a live token was cleared by the call"

    # …and a stray argument neither authenticates the call nor displaces what the connection holds.
    tool("whats_waiting")(token="vxa_mcp_FROM_AN_ARGUMENT")
    assert rig.CALL_TOKEN.get() == "vxa_mcp_LIVE", "a call argument displaced the connection's token"

    rig.CALL_TOKEN.set(None)
    out = json.loads(tool("whats_waiting")(token="vxa_mcp_FROM_AN_ARGUMENT"))
    assert out.get("authenticated") is False, "a call argument authenticated an anonymous connection"
    assert rig.CALL_TOKEN.get() is None


def test_rd21_friction_instance_wide_routes_are_gated(monkeypatch):
    """GATE 12 (R-D21). `friction_dump` returns the WHOLE instance's ledger — other people's
    workspace names, file paths, meeting ids and free text — and `friction_fixed` mutates it; both
    were open to any signed-in caller. And `friction_so_far` advertised "NO ACCOUNT NEEDED" one
    line above `me()`, so an anonymous agent read the refusal as an empty ledger and filed again.
    """
    as_user(monkeypatch, "7", admin=False)
    assert json.loads(tool("friction_dump")()).get("refused") == "operator only"
    assert json.loads(tool("friction_fixed")(["1"], "abc123")).get("refused") == "operator only"

    # The SUMMARY LINE is what a client shows and what a model acts on; the paragraph below it may
    # quote the old claim as history, and that is the point of keeping it.
    summary = (tool("friction_so_far").__doc__ or "").strip().splitlines()[0]
    assert "NO ACCOUNT NEEDED" not in summary
    assert "NEEDS AN ACCOUNT" in summary
