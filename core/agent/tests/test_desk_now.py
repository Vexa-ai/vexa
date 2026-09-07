"""Dated facts on a meeting page, and the desk README's `Now` read from them.

PRD decision 26.4 (`Now` = next meetings, open commitments) and decision 31 §3 (*"the write-back
phase files dated facts so the desk README's `Now` and the timeline agree"*). AGREE is the whole
requirement: both readers read the same three frontmatter keys, and neither parses a sentence.
"""
from __future__ import annotations

import datetime

import pytest

from shared import desk_now
from workspaces.shared.entities import EntityRefused, upsert_entity

UTC = datetime.timezone.utc
NOW = 1_788_362_400.0                       # 2026-09-02 15:20Z
HOUR = 3600.0


def _iso(epoch):
    return (datetime.datetime.fromtimestamp(epoch, UTC)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


# ── the write ────────────────────────────────────────────────────────────────────────────────────

def test_a_meeting_page_carries_when_it_happened(tmp_path):
    out = upsert_entity(tmp_path, "meeting", "ASWF DNA TSC", ["The TSC met for the first time."],
                        "the transcript", today="2026-09-02",
                        dates={"held_at": NOW - HOUR, "report_delivered_at": _iso(NOW)})
    text = (tmp_path / out["path"]).read_text()
    assert f"held_at: {_iso(NOW - HOUR)}" in text
    assert f"report_delivered_at: {_iso(NOW)}" in text
    assert out["dates"] == {"held_at": _iso(NOW - HOUR), "report_delivered_at": _iso(NOW)}


def test_an_epoch_and_an_iso_string_land_identically(tmp_path):
    a = upsert_entity(tmp_path / "a", "meeting", "M", ["x"], "s", dates={"held_at": NOW})
    b = upsert_entity(tmp_path / "b", "meeting", "M", ["x"], "s", dates={"held_at": _iso(NOW)})
    assert a["dates"] == b["dates"] == {"held_at": _iso(NOW)}


def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    out = upsert_entity(tmp_path, "meeting", "M", ["x"], "s",
                        dates={"held_at": "2026-09-02T15:20:00"})
    assert out["dates"]["held_at"] == "2026-09-02T15:20:00Z"


def test_a_key_outside_the_closed_set_is_dropped(tmp_path):
    """Frontmatter is a contract, not a scratchpad: `Now` can only be built on keys it knows."""
    out = upsert_entity(tmp_path, "meeting", "M", ["x"], "s",
                        dates={"held_at": NOW, "cancelled_at": NOW, "notes": "whatever"})
    assert out["dates"] == {"held_at": _iso(NOW)}
    assert "cancelled_at" not in (tmp_path / out["path"]).read_text()


def test_an_unparseable_date_is_dropped_not_written(tmp_path):
    out = upsert_entity(tmp_path, "meeting", "M", ["x"], "s", dates={"held_at": "thursday-ish"})
    assert out["dates"] == {}


def test_a_dates_only_call_changes_the_page_without_a_new_entry(tmp_path):
    """*The report went out* is a property of the meeting, not a new fact about it — the body is
    the record of what was LEARNED, and stamping it would grow the page on every delivery."""
    first = upsert_entity(tmp_path, "meeting", "M", ["The TSC met."], "the transcript",
                          today="2026-09-02", dates={"held_at": NOW - HOUR})
    before = (tmp_path / first["path"]).read_text()
    second = upsert_entity(tmp_path, "meeting", "M", [], "", dates={"report_delivered_at": NOW})
    after = (tmp_path / second["path"]).read_text()
    assert second["changed"] is True and second["facts_written"] == 0
    assert after.count("## 2026-09-02") == before.count("## 2026-09-02")
    assert f"report_delivered_at: {_iso(NOW)}" in after
    assert "The TSC met." in after


def test_the_same_date_twice_writes_nothing(tmp_path):
    """The write-back phase runs every turn; a no-op has to stay a no-op."""
    upsert_entity(tmp_path, "meeting", "M", ["x"], "s", dates={"held_at": NOW})
    again = upsert_entity(tmp_path, "meeting", "M", ["x"], "s", dates={"held_at": NOW})
    assert again["changed"] is False and again["dates"] == {}


def test_nothing_at_all_is_still_refused(tmp_path):
    with pytest.raises(EntityRefused):
        upsert_entity(tmp_path, "meeting", "M", [], "", dates={})


def test_a_fact_still_needs_a_source(tmp_path):
    with pytest.raises(EntityRefused):
        upsert_entity(tmp_path, "meeting", "M", ["The TSC met."], "", dates={"held_at": NOW})


# ── the read ─────────────────────────────────────────────────────────────────────────────────────

def _desk(tmp_path):
    upsert_entity(tmp_path, "meeting", "DNA TSC", ["It met."], "the transcript",
                  dates={"held_at": NOW - 2 * HOUR, "report_delivered_at": NOW - HOUR})
    upsert_entity(tmp_path, "meeting", "Weekly sync", ["It met."], "the transcript",
                  dates={"held_at": NOW - 3 * HOUR})
    upsert_entity(tmp_path, "meeting", "Board review", ["It is booked."], "the invite",
                  dates={"scheduled_at": NOW + 26 * HOUR})
    upsert_entity(tmp_path, "meeting", "Standup", ["It is booked."], "the invite",
                  dates={"scheduled_at": NOW + 2 * HOUR})
    return tmp_path


def test_now_reads_the_dates_the_phase_wrote(tmp_path):
    rows = desk_now.now_rows(_desk(tmp_path), now=NOW)
    assert [p["title"] for p in rows["next"]] == ["Standup", "Board review"]
    assert [p["title"] for p in rows["open"]] == ["Weekly sync"]


def test_a_delivered_write_up_closes_the_commitment(tmp_path):
    desk = _desk(tmp_path)
    assert "Weekly sync" in [p["title"] for p in desk_now.now_rows(desk, now=NOW)["open"]]
    upsert_entity(desk, "meeting", "Weekly sync", [], "", dates={"report_delivered_at": NOW})
    assert desk_now.now_rows(desk, now=NOW)["open"] == []


def test_a_meeting_that_ran_is_not_still_coming(tmp_path):
    """A calendar row can keep a future `scheduled_at` after the fact. `held_at` settles it."""
    upsert_entity(tmp_path, "meeting", "Moved", ["It ran early."], "the transcript",
                  dates={"scheduled_at": NOW + 5 * HOUR, "held_at": NOW - HOUR,
                         "report_delivered_at": NOW})
    assert desk_now.now_rows(tmp_path, now=NOW)["next"] == []


def test_an_old_undelivered_meeting_stops_asking(tmp_path):
    upsert_entity(tmp_path, "meeting", "Ancient", ["It met."], "the transcript",
                  dates={"held_at": NOW - 40 * 86400})
    assert desk_now.now_rows(tmp_path, now=NOW)["open"] == []


def test_pages_with_no_dates_and_templates_are_not_meetings(tmp_path):
    upsert_entity(tmp_path, "meeting", "Undated", ["It happened sometime."], "a note")
    folder = tmp_path / "kg" / "entities" / "meeting"
    (folder / "shape.md").write_text("---\ntype: meeting\ntitle: Shape\ntemplate: true\n"
                                     f"held_at: {_iso(NOW - HOUR)}\n---\n")
    assert desk_now.meetings(tmp_path) == []


def test_a_desk_with_no_meeting_pages_answers_empty(tmp_path):
    assert desk_now.meetings(tmp_path) == []
    assert desk_now.now_rows(tmp_path, now=NOW) == {"next": [], "open": [], "due": []}


def test_now_renders_links_not_prose(tmp_path):
    """Decision 26.4: the desk README is *a hub of links, not prose*."""
    out = desk_now.render_now(_desk(tmp_path), now=NOW, tz="Europe/Lisbon")
    assert "[[Standup]]" in out and "[[Weekly sync]]" in out
    assert "WEST" in out                                    # a time always carries its zone
    assert out.index("[[Standup]]") < out.index("[[Board review]]")
    assert "no write-up yet" in out


def test_an_empty_now_says_so_rather_than_vanishing(tmp_path):
    assert desk_now.render_now(tmp_path, now=NOW).strip() == "- Nothing scheduled."


# ── `due_at`: the last prose seam, closed (coordinator ruling, 2026-09-02) ───────────────────────
#
# A dated commitment used to reach the desk README because a regex found an ISO string in a bullet
# under a heading that happened to be called `## Committed`. It reaches it now because the
# write-back phase FILED it. The tests below are the difference stated twice: what a filed date can
# do, and what a written one no longer can.

def test_a_dated_commitment_is_a_field_the_phase_filed(tmp_path):
    upsert_entity(tmp_path, "decision", "Circulate the charter",
                  ["Cottalango asked for it before the next TSC."], "the 2026-09-02 transcript",
                  dates={"due_at": NOW + 18 * 24 * HOUR})
    rows = desk_now.now_rows(tmp_path, now=NOW)
    assert [p["title"] for p in rows["due"]] == ["Circulate the charter"]
    assert "[[Circulate the charter]]" in desk_now.render_now(tmp_path, now=NOW)


def test_a_date_written_in_PROSE_is_not_a_commitment(tmp_path):
    """The whole point. This page says a date, under the heading the old scraper matched, and `Now`
    does not show it — because nothing filed it, so nothing can move it or close it either."""
    out = upsert_entity(tmp_path, "meeting", "DNA TSC kickoff", ["It met."], "the transcript",
                        dates={"held_at": NOW - HOUR, "report_delivered_at": NOW})
    page = tmp_path / out["path"]
    page.write_text(page.read_text() + "\n## Committed\n\n- Circulate the charter by 2026-09-20\n")
    assert desk_now.now_rows(tmp_path, now=NOW)["due"] == []
    assert "Circulate the charter" not in desk_now.render_now(tmp_path, now=NOW)


def test_a_due_date_that_has_passed_is_not_Now(tmp_path):
    upsert_entity(tmp_path, "decision", "Overdue", ["x"], "s", dates={"due_at": NOW - HOUR})
    assert desk_now.now_rows(tmp_path, now=NOW)["due"] == []


def test_a_commitment_lives_on_whatever_page_owns_it(tmp_path):
    """All kinds, not only `meeting`: restricting the scan would silently drop every commitment
    filed on a project or a person."""
    for kind in ("project", "person", "company", "meeting"):
        upsert_entity(tmp_path, kind, f"Owner {kind}", ["x"], "s", dates={"due_at": NOW + HOUR})
    assert len(desk_now.now_rows(tmp_path, now=NOW)["due"]) == 4


def test_the_due_cap_holds(tmp_path):
    for i in range(desk_now.DUE_MAX + 4):
        upsert_entity(tmp_path, "decision", f"Item {i:02d}", ["x"], "s",
                      dates={"due_at": NOW + (i + 1) * HOUR})
    assert len(desk_now.now_rows(tmp_path, now=NOW)["due"]) == desk_now.DUE_MAX


def test_the_open_cap_holds(tmp_path):
    for i in range(desk_now.OPEN_MAX + 3):
        upsert_entity(tmp_path, "meeting", f"Held {i:02d}", ["x"], "s",
                      dates={"held_at": NOW - (i + 1) * HOUR})
    assert len(desk_now.now_rows(tmp_path, now=NOW)["open"]) == desk_now.OPEN_MAX


# ── across workspaces (PRD decision 26.2) ────────────────────────────────────────────────────────

def test_a_card_in_another_workspace_is_linked_by_id(tmp_path):
    """`Now` is part of a hub of links, so it obeys the same link rule as every other section: a
    card here is `[[Title]]`, a card in a group is `[[ws:<id>/<entity-id>]]` — which survives that
    group being renamed."""
    desk, grp = tmp_path / "desk", tmp_path / "grp"
    upsert_entity(desk, "meeting", "Mine", ["x"], "s", dates={"scheduled_at": NOW + HOUR})
    upsert_entity(grp, "meeting", "Theirs", ["x"], "s", dates={"scheduled_at": NOW + 2 * HOUR})
    mounts = [{"path": str(desk), "id": "aaaaaaaaaa"}, {"path": str(grp), "id": "bbbbbbbbbb"}]
    out = desk_now.render_now(mounts, now=NOW, home_id="aaaaaaaaaa")
    assert "[[Mine]]" in out
    assert "[[ws:bbbbbbbbbb/theirs]]" in out
    assert "[[Theirs]]" not in out


def test_a_single_root_still_works_unchanged(tmp_path):
    """The call shape every existing caller uses: a bare path, and every card is `[[Title]]`."""
    upsert_entity(tmp_path, "meeting", "Solo", ["x"], "s", dates={"scheduled_at": NOW + HOUR})
    assert "[[Solo]]" in desk_now.render_now(tmp_path, now=NOW)
