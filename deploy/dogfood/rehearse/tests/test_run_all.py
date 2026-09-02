"""`run_all` — the catalogue as the test, and what it does with a state that breaks."""
from __future__ import annotations

from rehearse import run_all
from rehearse.doors import DoorRefused
from rehearse.stub_doors import StubDoors


def test_the_whole_catalogue_is_green_offline(catalog, env):
    report = run_all.run(StubDoors(), catalog=catalog, env=env)
    assert report["failed"] == [], report["states"]
    assert report["ran"] == len(catalog.states) == report["passed"]


def test_it_reports_pass_fail_wall_time_and_the_link_per_state(catalog, env):
    report = run_all.run(StubDoors(), catalog=catalog, env=env)
    for row in report["states"]:
        assert set(("state", "as", "ok", "wall_s", "link")) <= set(row)
        assert row["wall_s"] >= 0
        assert row["link"].startswith("http"), row


def test_it_clicks_nothing(catalog, env):
    """Whether the link WORKS when a person clicks it is a walk, and a founder's judgment. What a
    suite can prove is everything up to the click."""
    doors = StubDoors()
    run_all.run(doors, catalog=catalog, env=env)
    assert not any(c[0].startswith("redeem") or c[0].startswith("click") for c in doors.calls)


def test_a_broken_state_fails_and_is_filed_as_friction(catalog, env):
    class NoMail(StubDoors):
        def await_mail(self, to, subject_contains="", budget_s=180, since=0.0):
            raise DoorRefused("no mail to that address arrived within 180s")

    filed = []
    report = run_all.run(NoMail(), catalog=catalog, env=env, reporter=filed.append)
    assert report["failed"], "every state ends at a mail; none of them can pass here"
    assert len(filed) == len(report["failed"])
    rec = filed[0]
    assert rec["severity"] == "blocker" and rec["tool"] == "rehearse"
    assert "no mail" in rec["what_went_wrong"]
    # The repro line is the whole value of the record: a fixing agent must be able to re-enter the
    # state without asking anybody how.
    assert "python -m rehearse.run_all --only" in rec["what_would_have_helped"]
    assert report["friction"][0]["filed"] is True


def test_a_friction_intake_that_is_down_does_not_lose_the_finding(catalog, env):
    def broken(_rec):
        raise RuntimeError("friction intake answered 503")

    class NoMail(StubDoors):
        def await_mail(self, to, subject_contains="", budget_s=180, since=0.0):
            raise DoorRefused("no mail arrived")
    report = run_all.run(NoMail(), catalog=catalog, env=env, reporter=broken)
    assert report["failed"]
    assert report["friction"][0]["filed"] is False
    assert "503" in report["friction"][0]["file_error"]


def test_only_runs_the_states_named(catalog, env):
    report = run_all.run(StubDoors(), catalog=catalog, env=env, only=["reply-pending"])
    assert [r["state"] for r in report["states"]] == ["reply-pending"]


def test_the_rendered_report_names_every_state_and_the_score(catalog, env):
    text = run_all.render(run_all.run(StubDoors(), catalog=catalog, env=env))
    for name in catalog.states:
        assert name in text
    assert f"{len(catalog.states)}/{len(catalog.states)} states green" in text
