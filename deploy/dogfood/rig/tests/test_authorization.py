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


def test_rd07_reaction_signal_is_operator_only(monkeypatch):
    """GATE 4a (R-D07). `reaction_signal` posts with the lane's admin key and never checked the
    reaction against the caller, while `reactions_list` handed out every id instance-wide — so
    `cancel` on a stranger's scheduled join was one call away for any signed-in user. It is the
    verb `_operator_or_refuse` was written for, and it was the one the gate was left off."""
    as_user(monkeypatch, "7", admin=False)
    out = json.loads(tool("reaction_signal")("r-1", "cancel"))
    assert out.get("refused") == "operator only"

    http = as_user(monkeypatch, "7", admin=True)
    tool("reaction_signal")("r-1", "cancel")
    assert any("/reactions/r-1/cancel" in u for u in http.urls())


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
    hand: it accepted a credential in a call argument even with VEXA_RIG_MODE=0, and it CLEARED a
    live token whenever the kwarg was absent — de-authenticating the first call every agent makes.
    """
    as_user(monkeypatch, "7")
    rig.CURRENT.set(None)
    rig.CALL_TOKEN.set("vxa_mcp_LIVE")

    tool("whats_waiting")()                    # no token= kwarg
    assert rig.CALL_TOKEN.get() == "vxa_mcp_LIVE", "a live token was cleared by the call"

    monkeypatch.setattr(rig, "RIG_MODE", False)
    rig.CALL_TOKEN.set(None)
    tool("whats_waiting")(token="vxa_mcp_FROM_AN_ARGUMENT")
    assert rig.CALL_TOKEN.get() is None, "a token argument authenticated with RIG_MODE off"


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
