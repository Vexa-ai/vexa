"""The queue, the claim book and the settings — the four surfaces the control MCP used to compute.

These modules are pure over a directory on purpose, which is what lets this file exist at all: the
same behaviour used to live in tool bodies on another host, reachable only with a docker socket
(seam inventory B1/B2/B6), and it had exactly zero tests.
"""
from __future__ import annotations

import json

import pytest

from control_plane import claims as claims_mod
from control_plane import person_settings
from control_plane import queue as queue_mod


@pytest.fixture()
def ws(tmp_path):
    d = tmp_path / "57"
    d.mkdir()
    return d


# ── the queue ────────────────────────────────────────────────────────────────────────────────
def test_a_person_with_no_meetings_and_no_scaffold_gets_the_welcome_and_nothing_else(ws):
    """THE WHOLE FIRST TURN, AND ONLY THIS. Returning the scaffold chore alongside the welcome
    turned hello into a research assignment and asked two questions in one breath."""
    out = queue_mod.build(subject="57", workspace=ws)
    assert out["waiting"] == 1
    assert [i["kind"] for i in out["items"]] == ["welcome"]
    assert "next_options" in out and "waiting" in out


def test_an_unscaffolded_person_with_meetings_is_asked_to_set_up(ws):
    out = queue_mod.build(subject="57", workspace=ws, meetings=[{"id": 1}])
    assert "setup" in {i["kind"] for i in out["items"]}


def test_a_live_bot_goes_to_the_front_of_the_queue(ws):
    (ws / ".scaffolded").write_text("{}")
    out = queue_mod.build(subject="57", workspace=ws, meetings=[{"id": 1}],
                          bots=[{"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                                 "status": "active"}],
                          reactions=[{"id": "r1", "status": "blocked", "flow": "f", "step": "s"}])
    assert out["items"][0]["kind"] == "live_now"
    assert out["next_options"] == queue_mod.MENU_LIVE


def test_a_failure_that_smells_of_our_own_plumbing_is_never_put_on_their_list(ws):
    """OURS OR THEIRS. A person can act on "waiting for your answer"; they cannot act on our SMTP
    credentials, and telling them about it hands them our plumbing as a chore."""
    (ws / ".scaffolded").write_text("{}")
    ours = queue_mod.build(subject="57", workspace=ws, meetings=[{"id": 1}], reactions=[
        {"id": "r1", "status": "failed", "flow": "f", "step": "s",
         "reason": "SMTP 535 authentication failed"}])
    theirs = queue_mod.build(subject="57", workspace=ws, meetings=[{"id": 1}], reactions=[
        {"id": "r2", "status": "failed", "flow": "f", "step": "s",
         "reason": "the organiser never answered the question"}])
    assert {i["kind"] for i in ours["items"]} >= {"ours_not_theirs"}
    assert {i["kind"] for i in theirs["items"]} >= {"stuck"}


def test_an_admin_can_change_the_copy_without_a_deploy(ws, tmp_path):
    """PRD §3.8 — every sentence a new person hears used to be a Python literal in an image."""
    g = tmp_path / "_global"
    (g / "queue").mkdir(parents=True)
    (g / "queue" / "welcome-anonymous.md").write_text("Say it our way.")
    out = queue_mod.anonymous_welcome(g)
    assert out["items"][0]["open_with_what_they_get"] == "Say it our way."
    assert queue_mod.anonymous_welcome(None)["items"][0]["open_with_what_they_get"] != "Say it our way."


def test_the_ours_or_theirs_list_is_data_an_admin_owns(ws, tmp_path):
    g = tmp_path / "_global"
    (g / "queue").mkdir(parents=True)
    (g / "queue" / "ours-not-theirs.md").write_text("# ours\nkaboom\n")
    (ws / ".scaffolded").write_text("{}")
    out = queue_mod.build(subject="57", workspace=ws, meetings=[{"id": 1}], global_dir=g,
                          reactions=[{"id": "r1", "status": "failed", "flow": "f", "step": "s",
                                      "reason": "kaboom in the mailer"}])
    assert {i["kind"] for i in out["items"]} >= {"ours_not_theirs"}


def test_an_unreadable_claim_book_never_fails_the_queue(ws):
    (ws / "_pending").mkdir()
    (ws / "_pending" / "claims.json").write_text("{not json")
    assert queue_mod.claims_of(ws) == []


# ── the claim book ───────────────────────────────────────────────────────────────────────────
def test_a_proposed_claim_is_not_context_until_a_human_answers(ws):
    claims_mod.propose(ws, [{"claim": "They ship on Fridays", "source": "their blog"}])
    assert claims_mod.context(ws)["validated"] == []
    assert claims_mod.context(ws)["still_proposed"] == 1


def test_answering_validates_and_marks_the_desk_ready_in_one_call(ws):
    """A human answering IS the workspace becoming ready. Marking it was a separate third call,
    which meant a person could answer everything and have nothing take effect."""
    ids = claims_mod.propose(ws, ["They ship on Fridays"])["ids"]
    res = claims_mod.record_verdicts(ws, [{"id": ids[0], "verdict": "confirmed"}])
    assert res["recorded"][0]["usable_as_context"] is True
    assert res.get("workspace_ready") is True
    assert (ws / ".scaffolded").is_file()
    assert len(claims_mod.context(ws)["validated"]) == 1


def test_an_unknown_verdict_is_refused_and_named(ws):
    ids = claims_mod.propose(ws, ["x"])["ids"]
    res = claims_mod.record_verdicts(ws, [{"id": ids[0], "verdict": "maybe"}])
    assert res["recorded"] == []
    assert res["errors"][0]["id"] == ids[0]


def test_scaffold_refuses_while_nothing_is_validated(ws):
    """Marking it ready with nothing in it means every artifact afterwards is written against an
    empty context and nobody finds out until they read one."""
    claims_mod.propose(ws, ["x"])
    st, body = claims_mod.scaffold(ws)
    assert st == 409 and body["still_proposed"] == 1
    assert not (ws / ".scaffolded").exists()


# ── the settings ─────────────────────────────────────────────────────────────────────────────
def test_defaults_are_filled_in_and_the_file_need_not_exist(ws):
    s = person_settings.read(ws)
    assert s["bot_name"] == "Vexa" and s["mail_minutes"] is True


def test_an_unknown_setting_is_refused_with_the_list(ws):
    """A setting that silently does nothing is worse than an error, and an agent with no vocabulary
    invents one."""
    st, body = person_settings.write(ws, "make_it_funnier", "yes")
    assert st == 422
    assert set(body["detail"]["the_settings_that_exist"]) == set(person_settings.VOCAB)


def test_an_on_off_setting_takes_the_words_a_person_uses(ws):
    for word, expected in (("off", False), ("yes", True), ("0", False)):
        st, body = person_settings.write(ws, "mail_minutes", word)
        assert st == 200 and body["settings"]["mail_minutes"] is expected


def test_a_timezone_that_is_not_a_zone_is_refused(ws):
    st, body = person_settings.write(ws, "timezone", "Mars/Olympus")
    assert st == 422 and "not a timezone" in body["detail"]["refused"]
    st, body = person_settings.write(ws, "timezone", "Europe/Lisbon")
    assert st == 200 and body["settings"]["timezone"] == "Europe/Lisbon"


def test_a_setting_write_leaves_the_other_settings_alone(ws):
    person_settings.write(ws, "bot_name", "Minutes")
    person_settings.write(ws, "mail_join", "on")
    raw = json.loads((ws / person_settings.SETTINGS_PATH).read_text())
    assert raw == {"bot_name": "Minutes", "mail_join": True}
