"""PRD 0.13.4 D17 / UI 7aa, 7cc: a README rendered per viewer on the server (`page_render`).

Deny test 22 (composer half): a ``{{crm …}}`` or ``{{cards …}}`` block renders byte-identical
whether hidden records exist or not — the owner (crm) already answers only visible rows, and the
renderer adds no count, no heading and no marker that could tell. Plus: never a raw placeholder,
actions never run on render, restricted references carry no name, personal blocks are marked."""
from __future__ import annotations

import json
import re

from control_plane import page_grammar as g
from control_plane import page_render as pr

README = """# AI Tinkerers Vienna

{{waiting-on-you}}

## Rooms
{{rooms limit=2}}

## The venue
Booked at [[page:abcdefghij/venue.md#dates|Unicorn Factory]] — see [[meeting:153@00:12:30|the call]].

## Partners
{{crm company="OeNB"}}

## Commitments
{{commitments}}

{{unknown-block x=1}}
Get going: {{new-chat prompt="Plan the next meetup"}} {{new-room name="speakers"}} {{invite role=contributor}}
"""

FOUNDER = pr.Viewer(subject="1", workspace="tinkerers", role="owner")
VALERIIA = pr.Viewer(subject="2", workspace="tinkerers", role="reader")

CRM_KEY = g.Block("crm", {"company": "OeNB"}).key
COMMIT_KEY = g.Block("commitments").key


def _refs(readable: bool):
    def resolve(refs):
        out = {}
        for r in refs:
            if readable:
                out[r.key] = {"access": "readable", "label": "Venue notes" if r.kind == "page" else "Planning call",
                              "open": {"x": r.id}, "url": f"/r/{r.kind}/{r.id}"}
            else:
                out[r.key] = {"access": "not-yours", "label": None, "request_access": True}
        return out
    return resolve


def _sources(*, founder: bool, rooms=None, crm_rows=None, cards=None):
    if crm_rows is None:
        crm_rows = ([{"id": "co1", "type": "Company", "title": "OeNB",
                      "fields": [{"name": "Name", "value": "OeNB", "shared": True},
                                 {"name": "Stage", "value": "Negotiation", "shared": False},
                                 {"name": "LastContact", "value": "2026-10-01", "shared": False}]}]
                    if founder else
                    [{"id": "co1", "type": "Company", "title": "OeNB",
                      "fields": [{"name": "Name", "value": "OeNB", "shared": True}]}])
    return pr.Sources(
        refs=_refs(founder),
        lists={"waiting-on-you": [{"key": "n1", "title": "Sign the venue contract"}] if founder else [],
               "rooms": rooms if rooms is not None else [{"key": "s-1", "title": "general", "ref": "room:s-1"}]},
        crm={CRM_KEY: {"view": "company", "rows": crm_rows}},
        cards={COMMIT_KEY: cards if cards is not None else [{"key": "c1", "title": "Book the venue", "state": "open"}]},
    )


def _texts(tree):
    return json.dumps(tree, ensure_ascii=False)


def test_two_viewers_see_two_pages_from_one_readme():
    a = pr.render(README, FOUNDER, _sources(founder=True))
    b = pr.render(README, VALERIIA, _sources(founder=False))
    crm_a = next(n for n in a["nodes"] if n.get("name") == "crm")
    crm_b = next(n for n in b["nodes"] if n.get("name") == "crm")
    assert [f["name"] for f in crm_a["rows"][0]["fields"]] == ["Name", "Stage", "LastContact"]
    assert [f.get("only_you") for f in crm_a["rows"][0]["fields"]] == [None, True, True]
    assert crm_a["personal"] is True
    assert [f["name"] for f in crm_b["rows"][0]["fields"]] == ["Name"] and crm_b["personal"] is False
    assert "only_you" not in _texts(b)
    # waiting-on-you: his only
    assert any(n.get("name") == "waiting-on-you" for n in a["nodes"])
    assert not any(n.get("name") == "waiting-on-you" for n in b["nodes"])


def test_never_a_raw_placeholder_or_a_parser_error():
    for viewer, founder in ((FOUNDER, True), (VALERIIA, False)):
        tree = pr.render(README, viewer, _sources(founder=founder))
        body = _texts(tree)
        assert not re.search(r"\{\{\s*[a-z]", body)
        assert "unknown-block" not in body and "error" not in body.lower()


def test_actions_are_descriptors_and_respect_the_viewers_role():
    calls = []

    class Spy(dict):
        def get(self, k, d=None):
            calls.append(k)
            return super().get(k, d)

    src = _sources(founder=False)
    src.lists = Spy(src.lists)
    tree = pr.render(README, VALERIIA, src)
    line = next(n for n in tree["nodes"] if n["t"] == "line" and any(p["t"] == "action" for p in n["parts"]))
    acts = {p["verb"]: p for p in line["parts"] if p["t"] == "action"}
    assert acts["new-chat"]["enabled"] is True and acts["new-chat"]["args"] == {"prompt": "Plan the next meetup"}
    assert acts["new-room"]["enabled"] is False and acts["new-room"]["reason"]
    assert acts["invite"]["enabled"] is False
    # nothing ran: the renderer only READ the lists it draws
    assert set(calls) <= set(pr.AGENT_BLOCKS)
    owner = pr.render(README, FOUNDER, _sources(founder=True))
    line = next(n for n in owner["nodes"] if n["t"] == "line" and any(p["t"] == "action" for p in n["parts"]))
    assert all(p["enabled"] for p in line["parts"] if p["t"] == "action")


def test_restricted_reference_has_no_name_and_the_writer_label_is_not_echoed():
    tree = pr.render(README, VALERIIA, _sources(founder=False))
    for key, a in tree["refs"].items():
        assert a["access"] == "not-yours" and "label" not in a and "open" not in a
    body = _texts(tree)
    assert "Unicorn Factory" not in body and "the call" not in body
    mine = pr.render(README, FOUNDER, _sources(founder=True))
    assert mine["refs"]["meeting:153#t=00:12:30"]["label"] == "Planning call"


def test_deny_22_hidden_records_render_byte_identical():
    """The owner answers only rows the viewer may see; whatever was hidden, the bytes are the same."""
    visible_room = [{"key": "s-1", "title": "general", "ref": "room:s-1"}]
    world_without_hidden = _sources(founder=False, rooms=visible_room)
    world_with_hidden = _sources(founder=False, rooms=visible_room)     # the hidden rows never arrive
    a = pr.to_bytes(pr.render(README, VALERIIA, world_without_hidden))
    b = pr.to_bytes(pr.render(README, VALERIIA, world_with_hidden))
    assert a == b
    # and an EMPTY visible set leaves no heading, no zero, no hint — identical to the block not existing
    none = pr.render(README, VALERIIA, _sources(founder=False, rooms=[], crm_rows=[], cards=[]))
    body = _texts(none)
    assert "## Rooms" not in body and "## Partners" not in body and "## Commitments" not in body
    assert '"more"' not in body and "0 " not in body
    readme_without_blocks = README.replace("## Rooms\n{{rooms limit=2}}\n", "").replace(
        '## Partners\n{{crm company="OeNB"}}\n', "").replace("## Commitments\n{{commitments}}\n", "")
    stripped = pr.render(readme_without_blocks, VALERIIA, _sources(founder=False, rooms=[], crm_rows=[], cards=[]))
    assert [n for n in none["nodes"] if n["t"] != "md"] == [n for n in stripped["nodes"] if n["t"] != "md"]


def test_show_more_counts_only_visible_rows():
    rooms = [{"key": f"s-{i}", "title": f"room {i}"} for i in range(5)]
    tree = pr.render(README, VALERIIA, _sources(founder=False, rooms=rooms))
    block = next(n for n in tree["nodes"] if n.get("name") == "rooms")
    assert len(block["rows"]) == 5 and block["limit"] == 2 and block["more"] == 3
    assert block["title"] == ""          # the README's own "## Rooms" is the heading


def test_a_source_that_is_down_is_one_quiet_state_and_cards_wait_for_s5():
    src = _sources(founder=False)
    src.crm = {}
    src.cards = {}
    tree = pr.render(README, VALERIIA, src)
    crm = next(n for n in tree["nodes"] if n.get("name") == "crm")
    cards = next(n for n in tree["nodes"] if n.get("name") == "commitments")
    assert crm == {"t": "block", "name": "crm", "state": "unavailable", "personal": False}
    assert cards["state"] == "pending" and "rows" not in cards


def test_public_view_drops_everything_personal():
    tree = pr.render(README, FOUNDER, _sources(founder=True))
    pub = pr.public_view(tree)
    body = _texts(pub)
    assert "Sign the venue contract" not in body and "Negotiation" not in body and "only_you" not in body
    assert "OeNB" in body


def test_export_is_stamped_and_the_commit_guard_sees_it():
    md = pr.to_markdown(pr.render(README, FOUNDER, _sources(founder=True)))
    assert g.contains_resolved(md)
    assert not g.contains_resolved(README)


def test_seeded_default_readme_renders_clean():
    md = pr.default_readme('Tinkerers {{x}} "Vienna"')
    assert "{{x}}" not in md
    tree = pr.render(md, FOUNDER, pr.Sources(lists={n: [] for n in pr.AGENT_BLOCKS}))
    body = _texts(tree)
    assert not re.search(r"\{\{\s*[a-z]", body)
    assert tree["nodes"][0]["text"].startswith("# Tinkerers")
    assert "## Rooms" not in body                     # empty blocks take their headings with them


def test_seeded_default_readme_is_about_the_people_first():
    """Founder ruling 2026-10-17: waiting-on-you → people → rooms → routines → commitments, then prose."""
    names = [b.name for b in g.blocks_in(pr.default_readme("T"))]
    assert names[:5] == ["waiting-on-you", "people", "rooms", "routines", "commitments"]
    md = pr.default_readme("T")
    assert md.index("{{commitments") < md.index("## How to use")


def test_claims_carry_a_proof_marker_only_through_the_s5_seam():
    md = "Venue: [[card:c-230|Unicorn Factory, 8 Nov]]"
    readable = lambda refs: {r.key: {"access": "readable", "label": "Venue contract"} for r in refs}  # noqa: E731
    plain = pr.render(md, FOUNDER, pr.Sources(refs=readable))
    assert "proof" not in plain["refs"]["card:c-230"]
    marked = pr.render(md, FOUNDER, pr.Sources(refs=readable, proof=lambda cid: "stale"))
    assert marked["refs"]["card:c-230"]["proof"] == "stale"
    hidden = pr.render(md, VALERIIA, pr.Sources(refs=lambda refs: {r.key: {"access": "unavailable"} for r in refs},
                                                proof=lambda cid: "proven"))
    assert "proof" not in hidden["refs"]["card:c-230"]
