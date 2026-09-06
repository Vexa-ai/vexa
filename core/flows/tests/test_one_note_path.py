"""ONE SPELLING OF A MEETING'S PAGE, AND THE REPORT LANDS IN IT (Vexa-ai/vexa#1601).

Founder, 2026-09-06, in a live Meet started from a chat with the transcript pinned and no document:
*"where is it?"*. The page is minted at bot-send now (agent-api, `control_plane/meeting_mint.py`),
and at row creation for a meeting that arrives from the mailbox with no chat at all. Whoever mints
it RECORDS the path on the meeting row.

That inverts this domain's job and this file is the record of the inversion. `_note_path`'s own
docstring has always said why: *"A path is not a thing two services can each be trusted to spell —
the day comes from the organiser's zone and the slug from an allow-list, and neither is guessable
from outside"*. It was written when this domain was the only writer. Now it is not, so:

  · `_note_path` ASKS THE ROW first, and composes only when nothing was recorded — which is every
    meeting that predates this, and where the recipe is unchanged;
  · the drop therefore writes into the page the person has had open for the whole meeting, and
    `_merge_meeting_doc` puts the report in its region and moves nothing else;
  · and `prepare_meeting` — the upcoming step, the mailbox's half — mints that page itself.

No network: `mt.meeting_row` and the agent door are replaced by fakes that record what was asked.
"""
from __future__ import annotations

import flows_defs.production as production
import pytest
from flows import Done, Reaction, Registry, StepCtx

from test_link_loop import FakeScaffolds, _StubDB

RECORDED = "kg/entities/meeting/2023-11-14-2313-minted-by-the-send.md"
COMPOSED = "kg/entities/meeting/2023-11-14-2313-pilot-sync.md"
INDEX = "kg/entities/meeting/index.md"
REPORT = "## Decided\n- ship it on the 21st"

REFS = {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync", "meeting_id": 97,
        "start": 1_700_003_600.0, "native": "n-97",
        "participants": ["anna@bank.test", "ben@bank.test"]}

PRIOR = {
    "process_meeting": {"report": REPORT, "group": "", "room_read": []},
    "email_minutes": {"message_id": "<m@x>", "link": "http://ui/?meeting=97"},
    "email_attendees": {"sent": 1, "meeting_id": 97, "to": ["ben@bank.test"], "drops": []},
}

#: EXACTLY what agent-api's `shared.meeting_doc.scaffold` writes at mint — the page this domain now
#: finds already on the desk. Inlined rather than imported because flows' image carries neither
#: `core/agent` nor `core/workspaces`; the shape is pinned on that side, and `gate:fact-parity`
#: pins the marker inside it.
MINTED = (
    "---\ntype: meeting\nmeeting: 97\nnative: n-97\ntitle: Pilot sync\ndate: 2023-11-14\n"
    "transcript_cursor:\n---\n\n# Pilot sync\n\n<!-- vexa:transcript meeting=97 -->\n\n"
    "## What this is about\n<!-- meeting:about:start -->\n\n<!-- meeting:about:end -->\n\n"
    "## Decisions\n<!-- meeting:decisions:start -->\n\n<!-- meeting:decisions:end -->\n\n"
    "## Commitments\n<!-- meeting:commitments:start -->\n\n<!-- meeting:commitments:end -->\n\n"
    "## People and companies\n<!-- meeting:people:start -->\n\n<!-- meeting:people:end -->\n\n"
    "## Open questions\n<!-- meeting:questions:start -->\n\n<!-- meeting:questions:end -->\n\n"
    "## Report\n<!-- meeting:report:start -->\n\n<!-- meeting:report:end -->\n"
)


def _ctx(refs: dict, prior: dict | None = None, scratch: dict | None = None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior=prior or {},
                   clock_now=1_700_000_000.0, scratch=scratch if scratch is not None else {})


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    monkeypatch.setattr(production, "setting", lambda uid, key: "")   # no timezone -> UTC
    return fake


class Rows:
    """The meetings domain, as a dict — and a ledger of what was asked of it."""

    def __init__(self, note_path: str | None = None):
        self.note_path = note_path
        self.asked: list[tuple] = []

    def __call__(self, uid, meeting_id, native=None):
        self.asked.append((str(uid), meeting_id, native))
        data: dict = {"title": "Pilot sync"}
        if self.note_path is not None:
            data["metadata"] = {"note_path": self.note_path}
        return {"id": 97, "native_meeting_id": "n-97", "data": data}


class Desks:
    """Every person's desk as a dict, plus the ledger of writes — `test_attendee_drop`'s Store,
    narrowed to what this file asserts on."""

    def __init__(self, seed: dict | None = None):
        self.files: dict[tuple[str, str], str] = dict(seed or {})
        self.writes: list[tuple[str, str]] = []

    def uid_of(self, email):
        return "uid-" + email.split("@")[0]

    def init(self, uid):
        pass

    def write(self, uid, path, content):
        self.writes.append((uid, path))
        self.files[(uid, path)] = content

    def read(self, uid, path, slug=None):
        return None if slug == "_global" else self.files.get((uid, path))

    def of(self, email, path):
        return self.files.get(("uid-" + email.split("@")[0], path))


def _rig(monkeypatch, desks: Desks, rows: Rows) -> Registry:
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production, "ensure_platform_user", desks.uid_of)
    monkeypatch.setattr(production, "ws_file", desks.read)
    monkeypatch.setattr(production.ag, "workspace_init", desks.init)
    monkeypatch.setattr(production.ag, "workspace_write", desks.write)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    return reg


# ── 1 · the recipe asks before it composes ──────────────────────────────────────────────────────

def test_the_row_says_where_its_page_is_and_the_recipe_takes_it(monkeypatch):
    rows = Rows(RECORDED)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    assert production._note_path(_ctx(dict(REFS)), "7", "Pilot sync") == RECORDED
    assert rows.asked == [("7", 97, "n-97")]


def test_a_meeting_nobody_minted_is_composed_exactly_as_it_always_was(monkeypatch):
    """The fallback, and it is every meeting that predates this. The stamp carries the meeting's
    TIME, not only its day (F58) — two occurrences on one day are still two files."""
    monkeypatch.setattr(production.mt, "meeting_row", Rows(None))
    assert production._note_path(_ctx(dict(REFS)), "7", "Pilot sync") == COMPOSED


@pytest.mark.parametrize("poisoned", [
    "kg/entities/meeting/../../../.ssh/authorized_keys",
    "kg/entities/meeting/sub/dir.md",
    "/etc/passwd",
    "kg/entities/meeting/index.md",
    "../secrets.md",
])
def test_a_recorded_path_that_is_not_ours_is_refused_and_the_recipe_answers(monkeypatch, poisoned):
    """The record rides an annotation an account's own API key can write, and this path names a file
    written onto EVERY desk in the room. The guard is the alphabet, and the answer when it fails is
    the composition — never nothing, and never the poisoned name."""
    monkeypatch.setattr(production.mt, "meeting_row", Rows(poisoned))
    assert production._note_path(_ctx(dict(REFS)), "7", "Pilot sync") == COMPOSED


def test_the_row_is_asked_once_per_reaction(monkeypatch):
    """Several moments want this path inside one run — every mail's `_scaffold_refs`, then the drop
    — and a lookup that failed and later succeeded would name two different files in one reaction.
    Same rule, same home (`ctx.scratch`) as `_meeting_stamp`."""
    rows = Rows(RECORDED)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    ctx = _ctx(dict(REFS))
    for _ in range(3):
        assert production._note_path(ctx, "7", "Pilot sync") == RECORDED
    assert len(rows.asked) == 1


def test_a_meetings_domain_that_cannot_be_reached_costs_the_read_not_the_step(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(production.mt, "meeting_row", boom)
    assert production._note_path(_ctx(dict(REFS)), "7", "Pilot sync") == COMPOSED


def test_the_scaffold_carries_the_recorded_path_so_the_link_opens_the_real_page(monkeypatch):
    monkeypatch.setattr(production.mt, "meeting_row", Rows(RECORDED))
    assert production._scaffold_refs(_ctx(dict(REFS)), "7")["note_path"] == RECORDED


def test_the_caller_may_name_the_row_when_refs_do_not_carry_one(monkeypatch):
    """`prepare_meeting` plans the row itself, moments before it mints its scaffold — without being
    told the id the read-back has nothing to ask about."""
    rows = Rows(RECORDED)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    refs = {k: v for k, v in REFS.items() if k not in ("meeting_id", "native")}
    assert production._scaffold_refs(_ctx(refs), "7", meeting_id="97")["note_path"] == RECORDED
    assert rows.asked == [("7", "97", None)]


# ── 2 · the report lands in the page that is already there ──────────────────────────────────────

def test_the_drop_writes_into_the_page_the_send_minted(monkeypatch):
    """The whole point. The person has had this page open for the meeting; the report joins it."""
    desks = Desks({("uid-anna", RECORDED): MINTED, ("uid-ben", RECORDED): MINTED})
    reg = _rig(monkeypatch, desks, Rows(RECORDED))
    out = reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert isinstance(out, Done) and out.result["dropped"] == 2
    assert {p for _u, p in desks.writes} == {RECORDED, INDEX}
    assert desks.of("ben@bank.test", COMPOSED) is None, "no second page beside the minted one"


def test_the_report_lands_in_its_region_and_nothing_else_on_the_page_moves(monkeypatch):
    desks = Desks({("uid-anna", RECORDED): MINTED, ("uid-ben", RECORDED): MINTED})
    reg = _rig(monkeypatch, desks, Rows(RECORDED))
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    after = desks.of("ben@bank.test", RECORDED)
    assert "<!-- meeting:report:start -->\n## Decided\n- ship it on the 21st\n" in after
    # the widget, the frontmatter and every other region are byte-identical to the minted page
    head, tail = MINTED.split("<!-- meeting:report:start -->")
    assert after.startswith(head) and after.endswith(tail.lstrip("\n"))


def test_a_page_a_person_grew_during_the_meeting_keeps_every_byte_but_the_report(monkeypatch):
    grown = MINTED.replace("<!-- meeting:decisions:start -->",
                           "<!-- meeting:decisions:start -->\n- ship it on the 21st") \
                  + "\nI still owe Cara the migration doc.\n"
    desks = Desks({("uid-anna", RECORDED): grown, ("uid-ben", RECORDED): grown})
    reg = _rig(monkeypatch, desks, Rows(RECORDED))
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    after = desks.of("ben@bank.test", RECORDED)
    assert "I still owe Cara the migration doc." in after
    assert "<!-- meeting:decisions:start -->\n- ship it on the 21st" in after
    assert "vexa:transcript meeting=97" in after
    assert "ship it on the 21st" in after.split("<!-- meeting:report:start -->")[1]


def test_an_attendee_with_no_minted_page_gets_the_whole_artefact_at_the_recorded_name(monkeypatch):
    """A guest was never in the sender's chat, so nothing minted them a page. They get the drop's
    own composition of the entity — at the ONE name, so the room's copies agree."""
    desks = Desks({("uid-anna", RECORDED): MINTED})
    reg = _rig(monkeypatch, desks, Rows(RECORDED))
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    theirs = desks.of("ben@bank.test", RECORDED)
    assert "ship it on the 21st" in theirs and "vexa:transcript meeting=97" in theirs
    assert "participants: [" in theirs, "the full entity, not a merge into a page that is not there"


def test_a_meeting_nobody_minted_drops_exactly_where_it_always_did(monkeypatch):
    desks = Desks()
    reg = _rig(monkeypatch, desks, Rows(None))
    reg.steps["drop_to_attendees"](_ctx(dict(REFS), PRIOR))
    assert desks.of("ben@bank.test", COMPOSED) is not None


# ── 3 · a meeting that arrives with no chat mints its own page ──────────────────────────────────

def test_the_upcoming_step_mints_the_page_when_the_row_is_created(monkeypatch, scaffolds):
    """The mailbox's half. No chat ever sent a bot, so nothing bound one and agent-api was never on
    the path — the flow asks for the same act at the moment the row exists, and the prepare mail's
    link then carries the page that was just made rather than a name nothing had written."""
    minted: list[tuple] = []

    def mint(uid, meeting_id, path=""):
        minted.append((uid, str(meeting_id), path))
        return RECORDED

    rows = Rows(None)
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production.ag, "mint_meeting_note", mint)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    monkeypatch.setattr(production.mt, "ensure_meeting_row",
                        lambda uid, url, title=None, start=None: "97")
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    monkeypatch.setattr(production, "notify", lambda *a, **k: "<mid@x>")
    monkeypatch.setattr(production.mx, "register_thread", lambda *a, **k: None)
    monkeypatch.setattr(production.mailtext, "render", lambda *a, **k: ("Prepare: T", "body"))
    out = reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync",
         "start": 1_700_003_600.0, "url": "https://meet.google.com/abc-defg-hij"}))
    assert isinstance(out, Done) and out.result["meeting_ref"] == "97"
    assert minted == [("7", "97", "")], "one mint, for the row that was just planned"


def test_the_prepare_mails_page_is_the_one_that_was_just_minted(monkeypatch, scaffolds):
    """The scaffold's `refs.note_path` is READ BACK off the row the mint recorded on, not composed —
    which is what makes the link's Brief tab open the page instead of a guess."""
    rows = Rows(None)

    def mint(uid, meeting_id, path=""):
        rows.note_path = RECORDED       # agent-api recorded it on the row, as it does in production
        return RECORDED

    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production.ag, "mint_meeting_note", mint)
    monkeypatch.setattr(production.mt, "meeting_row", rows)
    monkeypatch.setattr(production.mt, "ensure_meeting_row",
                        lambda uid, url, title=None, start=None: "97")
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    monkeypatch.setattr(production, "notify", lambda *a, **k: "<mid@x>")
    monkeypatch.setattr(production.mx, "register_thread", lambda *a, **k: None)
    monkeypatch.setattr(production.mailtext, "render", lambda *a, **k: ("Prepare: T", "body"))
    reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync",
         "start": 1_700_003_600.0, "url": "https://meet.google.com/abc-defg-hij"}))
    assert scaffolds.for_("anna@bank.test")["refs"]["note_path"] == RECORDED


def test_a_mint_that_could_not_happen_never_costs_the_prepare_mail(monkeypatch, scaffolds):
    """A prepare mail with a link is worth more than a page, and the page still arrives when the
    meeting ends. `ag.mint_meeting_note` swallows its own failures for exactly this."""
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production.ag, "mint_meeting_note", lambda *a, **k: "")
    monkeypatch.setattr(production.mt, "meeting_row", Rows(None))
    monkeypatch.setattr(production.mt, "ensure_meeting_row",
                        lambda uid, url, title=None, start=None: "97")
    monkeypatch.setattr(production, "setting",
                        lambda uid, key: True if key == "mail_prep" else "")
    sent: list = []
    monkeypatch.setattr(production, "notify", lambda *a, **k: sent.append(a) or "<mid@x>")
    monkeypatch.setattr(production.mx, "register_thread", lambda *a, **k: None)
    monkeypatch.setattr(production.mailtext, "render", lambda *a, **k: ("Prepare: T", "body"))
    out = reg.steps["prepare_meeting"](_ctx(
        {"uid": "7", "organizer": "anna@bank.test", "title": "Pilot sync",
         "start": 1_700_003_600.0, "url": "https://meet.google.com/abc-defg-hij"}))
    assert isinstance(out, Done) and len(sent) == 1
    assert scaffolds.for_("anna@bank.test")["refs"]["note_path"] == COMPOSED
