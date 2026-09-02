"""The desk README as a HUB OF LINKS — founder refinement, 2026-09-02:
*"we want the personal desk readme to be the thing where they have what they generally need —
mostly links to the other cards in different workspaces."*

Four claims, and the second matters most:

  1. the sections are links to cards, across every mounted workspace, ordered by USE;
  2. **`## Pinned` is the person's and is never regenerated** — the marker around it exists to tell
     the generator where to stop, not to license writing there;
  3. nothing outside the markers is touched either;
  4. the caps hold, and a cross-workspace card is linked in `ws:` id form so it survives a rename.
"""
from __future__ import annotations

import datetime
import time

from shared import desk_now, desk_readme
from shared.entities import upsert_entity
from shared.workspace_id import write_workspace_json

DESK_ID = "aaaaaaaaaa"
GROUP_ID = "bbbbbbbbbb"
NOW = 1_788_362_400.0                       # 2026-09-02 15:20Z — the same instant test_desk_now uses
HOUR = 3600.0


def _ws(tmp_path, name, wid, kind="desk"):
    d = tmp_path / name
    (d / "kg" / "entities").mkdir(parents=True)
    write_workspace_json(d, id=wid, kind=kind, created="2026-09-02")
    return d


def _desk(tmp_path):
    return _ws(tmp_path, "desk", DESK_ID)


def _sections(text: str) -> dict:
    out = {}
    for key, _ in desk_readme.SECTIONS:
        s, e = f"<!-- desk:{key}:start -->", f"<!-- desk:{key}:end -->"
        i, j = text.find(s), text.find(e)
        out[key] = text[i + len(s): j] if i != -1 and j != -1 else None
    return out


def _write(d, mounts=(), workspaces=(), touches=None, today="2026-09-02", name="", now=None):
    return desk_readme.update_readme(d, mounts=mounts or [{"path": str(d), "id": DESK_ID}],
                                     workspaces=workspaces, touches=touches, home_id=DESK_ID,
                                     name=name, today=today, now=now)


# ── the shape ────────────────────────────────────────────────────────────────────────────────────

def test_a_fresh_desk_gets_every_section_in_order(tmp_path):
    d = _desk(tmp_path)
    assert _write(d, name="olga@spi.com")["changed"] is True
    text = (d / "README.md").read_text()
    got = _sections(text)
    assert all(got[k] is not None for k, _ in desk_readme.SECTIONS)
    assert [k for k, _ in desk_readme.SECTIONS] == ["pinned", "now", "people", "companies",
                                                    "projects", "workspaces", "recent"]
    # …and in that order on the page, not merely present
    at = [text.index(f"<!-- desk:{k}:start -->") for k, _ in desk_readme.SECTIONS]
    assert at == sorted(at)


def test_the_header_is_two_lines_of_prose_and_is_written_once(tmp_path):
    d = _desk(tmp_path)
    _write(d, name="olga@spi.com")
    head = (d / "README.md").read_text().split("<!-- desk:pinned:start -->")[0].strip()
    assert head.startswith("# olga@spi.com — desk")
    assert len([ln for ln in head.splitlines() if ln.strip()]) == 2
    # a later run does not rewrite it, even if the name changed
    _write(d, name="somebody.else@spi.com")
    assert (d / "README.md").read_text().startswith("# olga@spi.com — desk")


def test_the_page_is_links_not_prose(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting", today="2026-09-02")
    _write(d)
    body = "\n".join(_sections((d / "README.md").read_text())["people"].splitlines())
    rows = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("##")]
    assert rows == ["", "- [[Olga Avramenko]]"] or rows == ["- [[Olga Avramenko]]"]


# ── Pinned is theirs ─────────────────────────────────────────────────────────────────────────────

def test_pinned_is_seeded_empty_with_a_hint_and_then_never_touched(tmp_path):
    d = _desk(tmp_path)
    _write(d)
    text = (d / "README.md").read_text()
    assert "## Pinned" in _sections(text)["pinned"]
    assert "Yours." in _sections(text)["pinned"]

    mine = "## Pinned\n\n- [[ws:bbbbbbbbbb/the-charter]]\n- my own note\n"
    text = text.replace(_sections(text)["pinned"], "\n" + mine)
    (d / "README.md").write_text(text)

    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting", today="2026-09-02")
    _write(d)
    after = _sections((d / "README.md").read_text())
    assert "- my own note" in after["pinned"] and "[[ws:bbbbbbbbbb/the-charter]]" in after["pinned"]
    assert "Yours." not in after["pinned"]                  # the hint went when they wrote over it
    assert "[[Olga Avramenko]]" in after["people"]           # …and the rest still regenerated


def test_text_outside_the_markers_is_never_touched(tmp_path):
    d = _desk(tmp_path)
    header = ("# Olga's desk\n\nWhat I care about this quarter is the DNA charter.\n\n"
              "## My own section\n\n- something I typed by hand\n\n")
    (d / "README.md").write_text(header)
    _write(d)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting", today="2026-09-02")
    _write(d)
    text = (d / "README.md").read_text()
    assert text.startswith(header.rstrip("\n"))
    assert "- something I typed by hand" in text and "[[Olga Avramenko]]" in text


def test_regeneration_replaces_only_between_the_markers(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting", today="2026-09-02")
    _write(d)
    upsert_entity(d, "person", "Cottalango Leon", ["Chairs."], "the meeting", today="2026-09-02")
    _write(d)
    text = (d / "README.md").read_text()
    assert text.count("<!-- desk:people:start -->") == 1
    assert "[[Cottalango Leon]]" in text and "[[Olga Avramenko]]" in text


def test_it_is_idempotent(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "the meeting", today="2026-09-02")
    assert _write(d, now=NOW)["changed"] is True
    assert _write(d, now=NOW)["changed"] is False


# ── across workspaces ────────────────────────────────────────────────────────────────────────────

def test_a_card_in_another_workspace_is_linked_in_id_form(tmp_path):
    d, g = _desk(tmp_path), _ws(tmp_path, "grp", GROUP_ID, kind="group")
    upsert_entity(d, "person", "Olga Avramenko", ["Attends."], "s", today="2026-09-02")
    upsert_entity(g, "person", "Cottalango Leon", ["Chairs."], "s", today="2026-09-02")
    upsert_entity(g, "company", "Sony Pictures Imageworks", ["Employer."], "s", today="2026-09-02")
    _write(d, mounts=[{"path": str(d), "id": DESK_ID}, {"path": str(g), "id": GROUP_ID}])
    got = _sections((d / "README.md").read_text())
    assert "- [[Olga Avramenko]]" in got["people"]                        # ours — the plain form
    assert f"- [[ws:{GROUP_ID}/cottalango-leon]]" in got["people"]        # theirs — the id form
    assert f"- [[ws:{GROUP_ID}/sony-pictures-imageworks]]" in got["companies"]


def test_a_mount_with_no_id_contributes_nothing(tmp_path):
    """A card whose link cannot be written is worse on this page than a card that is absent — a hub
    of links whose links do not resolve is not a hub."""
    d = _desk(tmp_path)
    plain = tmp_path / "unmigrated"
    (plain / "kg" / "entities").mkdir(parents=True)
    upsert_entity(plain, "person", "Nobody Yet", ["x"], "s", today="2026-09-02")
    _write(d, mounts=[{"path": str(d), "id": DESK_ID}, {"path": str(plain), "id": ""}])
    assert "Nobody Yet" not in (d / "README.md").read_text()


def test_workspaces_are_listed_by_id_link(tmp_path):
    d = _desk(tmp_path)
    _write(d, workspaces=[{"id": GROUP_ID, "name": "ASWF DNA Project"}])
    got = _sections((d / "README.md").read_text())
    assert f"- [[ws:{GROUP_ID}/README.md]]" in got["workspaces"]
    assert "ASWF DNA Project" not in got["workspaces"]     # the name is resolved at read time


# ── Now — ONE implementation, and it is `shared/desk_now.py` ─────────────────────────────────────
#
# Coordinator ruling, 2026-09-02: there were two. This module's version scraped a meeting's date out
# of its title or body and matched `## Committed`-shaped headings for commitments; `desk_now` reads
# frontmatter facts that `entity_upsert(dates=)` filed. The second is right — a date found in a
# sentence is whatever a model last typed, so moving a meeting could not move the README — and the
# first is gone. These tests assert through the seam that survived.

def test_now_leads_with_the_next_meetings_soonest_first(tmp_path):
    d = _desk(tmp_path)
    for offset in (30 * 24, 13 * 24, -200 * 24):
        upsert_entity(d, "meeting", f"DNA TSC {offset}", ["Booked."], "s",
                      dates={"scheduled_at": NOW + offset * HOUR})
    got = _sections(_write(d, now=NOW) and (d / "README.md").read_text())["now"]
    assert got.index("[[DNA TSC 312]]") < got.index("[[DNA TSC 720]]")
    assert "[[DNA TSC -4800]]" not in got                   # behind us — not "Now"


def test_now_carries_a_commitment_only_when_a_FIELD_carries_its_date(tmp_path):
    """The seam the ruling closed. The page below says a date under the heading the old scraper
    matched, and it does not reach `Now`; the one beside it filed `due_at` and does."""
    d = _desk(tmp_path)
    out = upsert_entity(d, "meeting", "DNA TSC kickoff", ["Held."], "s",
                        dates={"held_at": NOW - HOUR, "report_delivered_at": NOW})
    page = d / out["path"]
    page.write_text(page.read_text() + "\n## Committed\n\n- Circulate the charter by 2026-09-20\n")
    upsert_entity(d, "decision", "Sign the CLA", ["SPI asked for the standard shape."], "the call",
                  dates={"due_at": NOW + 5 * 24 * HOUR})
    _write(d, now=NOW)
    got = _sections((d / "README.md").read_text())["now"]
    assert "[[Sign the CLA]]" in got and "due " in got
    assert "Circulate the charter" not in got


def test_a_held_meeting_with_no_write_up_is_an_open_commitment(tmp_path):
    d = _desk(tmp_path)
    upsert_entity(d, "meeting", "Weekly sync", ["It met."], "the transcript",
                  dates={"held_at": NOW - 2 * HOUR})
    got = _sections(_write(d, now=NOW) and (d / "README.md").read_text())["now"]
    assert "[[Weekly sync]]" in got and "no write-up yet" in got


def test_the_meeting_cap_holds(tmp_path):
    d = _desk(tmp_path)
    for i in range(desk_now.AHEAD_MAX + 4):
        upsert_entity(d, "meeting", f"Sync {i:02d}", ["x"], "s",
                      dates={"scheduled_at": NOW + (i + 1) * 24 * HOUR})
    got = _sections(_write(d, now=NOW) and (d / "README.md").read_text())["now"]
    assert len([ln for ln in got.splitlines() if ln.startswith("- ") and " — [[Sync" in ln]) \
        == desk_now.AHEAD_MAX


def test_a_meeting_in_the_group_appears_in_Now_by_id(tmp_path):
    """`Now` obeys the hub's link rule like every other section."""
    d, g = _desk(tmp_path), _ws(tmp_path, "grp", GROUP_ID, kind="group")
    upsert_entity(g, "meeting", "Group standup", ["Booked."], "s",
                  dates={"scheduled_at": NOW + 2 * HOUR})
    _write(d, mounts=[{"path": str(d), "id": DESK_ID}, {"path": str(g), "id": GROUP_ID}], now=NOW)
    assert f"[[ws:{GROUP_ID}/group-standup]]" in _sections((d / "README.md").read_text())["now"]


# ── ordering by USE, and the caps ────────────────────────────────────────────────────────────────

def test_cards_are_ordered_by_what_the_person_opened_not_by_what_the_agent_wrote(tmp_path):
    d = _desk(tmp_path)
    for who in ("Aaa Person", "Bbb Person", "Ccc Person"):
        upsert_entity(d, "person", who, ["x"], "s", today="2026-09-02")
    touches = [{"workspace": DESK_ID, "path": "kg/entities/person/ccc-person.md", "at": time.time()},
               {"workspace": DESK_ID, "path": "kg/entities/person/aaa-person.md", "at": time.time() - 60}]
    _write(d, touches=touches)
    rows = [ln for ln in _sections((d / "README.md").read_text())["people"].splitlines()
            if ln.startswith("- ")]
    assert rows[0] == "- [[Ccc Person]]" and rows[1] == "- [[Aaa Person]]"


def test_the_card_cap_holds_per_section(tmp_path):
    d = _desk(tmp_path)
    for i in range(desk_readme.CARDS_MAX + 6):
        upsert_entity(d, "person", f"Person Number{i:03d}", ["x"], "s", today="2026-09-02")
    _write(d)
    rows = [ln for ln in _sections((d / "README.md").read_text())["people"].splitlines()
            if ln.startswith("- ")]
    assert len(rows) == desk_readme.CARDS_MAX


# ── Recently opened ──────────────────────────────────────────────────────────────────────────────

def test_recently_opened_names_cards_and_falls_back_to_a_path(tmp_path):
    d, g = _desk(tmp_path), _ws(tmp_path, "grp", GROUP_ID, kind="group")
    upsert_entity(g, "person", "Cottalango Leon", ["Chairs."], "s", today="2026-09-02")
    touches = [{"workspace": GROUP_ID, "path": "kg/entities/person/cottalango-leon.md", "at": 3},
               {"workspace": GROUP_ID, "path": "notes/2026-03-02.md", "at": 2},
               {"workspace": DESK_ID, "path": "scratch.md", "at": 1}]
    _write(d, mounts=[{"path": str(d), "id": DESK_ID}, {"path": str(g), "id": GROUP_ID}],
           touches=touches)
    got = _sections((d / "README.md").read_text())["recent"]
    assert f"- [[ws:{GROUP_ID}/cottalango-leon]]" in got          # a card, named
    assert f"- [[ws:{GROUP_ID}/notes/2026-03-02.md]]" in got      # no entity id — the path form
    assert "- `scratch.md`" in got                                 # ours — a plain workspace path


def test_the_recent_cap_holds(tmp_path):
    d = _desk(tmp_path)
    touches = [{"workspace": DESK_ID, "path": f"n{i}.md", "at": i} for i in range(desk_readme.RECENT_MAX + 5)]
    _write(d, touches=touches)
    rows = [ln for ln in _sections((d / "README.md").read_text())["recent"].splitlines()
            if ln.startswith("- ")]
    assert len(rows) == desk_readme.RECENT_MAX


# ── the shape it replaced ────────────────────────────────────────────────────────────────────────

def test_the_retired_sections_are_removed_on_sight(tmp_path):
    """A desk generated by the earlier shape converges instead of carrying dead blocks forever.
    Purpose / Objective / Where-things-stand belong to a GROUP's README — a group has a purpose, a
    desk is a person."""
    d = _desk(tmp_path)
    (d / "README.md").write_text(
        "# Olga — desk\n\n"
        "<!-- desk:meetings:start -->\n## Meetings\n\n- [[Old]]\n<!-- desk:meetings:end -->\n\n"
        "<!-- desk:purpose:start -->\n## Purpose\n\n(unset)\n<!-- desk:purpose:end -->\n")
    _write(d)
    text = (d / "README.md").read_text()
    for key in desk_readme.RETIRED_SECTIONS:
        assert f"desk:{key}:" not in text
    assert "## Meetings" not in text and "## Purpose" not in text
    assert "# Olga — desk" in text and "## Now" in text


def test_empty_is_said_never_omitted(tmp_path):
    """An absent section reads as 'not looked at', and the reader cannot tell that from
    'nothing there'."""
    d = _desk(tmp_path)
    _write(d)
    got = _sections((d / "README.md").read_text())
    assert "No people on this desk yet" in got["people"]
    assert "No projects on this desk yet" in got["projects"]
    assert "belongs to no group workspace yet" in got["workspaces"]
    assert "Nothing opened from here yet" in got["recent"]
    assert "Nothing scheduled." in got["now"]         # `desk_now`'s words, because it is the renderer
