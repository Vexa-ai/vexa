"""THE PAGE IS THE PROOF — a flow written from the governance chat has one (Vexa-ai/vexa#1639).

Founder, 2026-09-06, after being told the gate was open and the agent still had no instruction:
*"we want to be able to write flows for the global chat as we like."*

`flows_submit` could already file a flow as data and have the worker running it about ten seconds
later. What it had no answer for was the sentence after: *where do I read what I just made.* The
image's flows each have a generated page in `_global/flows/` (#1615/#1626); a flow the admin
authored had none, so the only way to see one was to ask the API for a JSON row.

Six claims, in the order they would fail:

  P1  A runtime-authored version renders the SAME page shape as an image flow, at its own filename
      `<flow>@<version>.md`, and that name can never collide with the seeded `<flow>.md`.
  P2  It carries the three facts only a runtime version has — who activated it, when, and whether
      it is still the version new facts react on.
  P3  EDITING IS A NEW VERSION. Both pages stay, and the retired one says so in its first line and
      names the version that replaced it. A page that quietly disappeared would leave the admin
      unable to see what changed, which is the whole reason a step list is versioned and never
      edited in place.
  P4  A row naming a step this image does not carry still renders — with the warning `_step_facts`
      already writes. A page that failed to render is a flow with no proof at all.
  P5  The route serves them, in two shapes: the POLL shape (`bodies=0`, an etag per page) and the
      full one (`only=…`). The writer on the other side is a loop; it must be able to ask "has
      anything changed" without carrying every step's source across every four seconds.
  P6  The etag is of the CONTENT. A step whose docstring changed produces a different page for an
      unchanged row, and the writer has to notice.
"""
from __future__ import annotations

import json
import os

import pytest
from sqlite_double import SqliteDB

import flows_pages

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-author-pages",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://author-pages:unreachable@127.0.0.1:1/flows"}

T0 = 1_788_687_000.0            # 2026-09-06 09:30Z, as `clock.now()` writes it


@pytest.fixture(scope="module")
def api():
    """The real app through `TestClient`, a real `SqliteDB` swapped under it, no network — the
    composition every other route test in this suite uses."""
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    flows_api.db = SqliteDB()
    return flows_api, TestClient(flows_api.app, raise_server_exceptions=False)


def _row(name: str, version: int, on: str, steps: list, *, status: str = "active",
         by: str = "admin-1", at: float = T0) -> dict:
    return {"name": name, "version": version, "on": on, "steps": list(steps),
            "params": {}, "status": status, "created_by": by, "created_at": at, "source": "api"}


def _submit(flows_api, row: dict) -> None:
    flows_api.db.execute(
        """INSERT INTO flow_version (name, version, on_event, steps, params, status, created_by,
                                     created_at)
           VALUES (:n,:v,:e,:s,'{}',:st,:by,:t)""",
        {"n": row["name"], "v": row["version"], "e": row["on"], "s": json.dumps(row["steps"]),
         "st": row["status"], "by": row["created_by"], "t": row["created_at"]})


@pytest.fixture(autouse=True)
def rows(api):
    flows_api, _ = api
    flows_api.db.execute("DELETE FROM flow_version")
    yield
    flows_api.db.execute("DELETE FROM flow_version")


@pytest.fixture(scope="module")
def reg():
    return flows_pages.build_registry()


# ── P1/P2 · the page of a flow somebody wrote ───────────────────────────────────────────────────

def test_a_runtime_version_gets_a_page_at_its_own_versioned_filename(reg):
    row = _row("mail_the_ops_team", 1, "meeting.completed", ["process_meeting", "email_minutes"])
    [page] = flows_pages.runtime_pages(reg, [row])
    assert page["file"] == "mail_the_ops_team@1.md"
    assert page["file"] == flows_pages.page_file("mail_the_ops_team", 1)
    body = page["body"]
    assert body.startswith("---\n")
    assert f"kind: {flows_pages.KIND}\n" in body
    assert "trigger: meeting.completed" in body
    assert "steps: 2" in body
    # The step docstrings, in order — the same body an image page carries, from the same code.
    assert body.index("### 1. `process_meeting`") < body.index("### 2. `email_minutes`")
    assert "## The code" in body and "<ViewSource step=\"process_meeting\">" in body


def test_the_versioned_name_can_never_collide_with_a_seeded_image_page(reg):
    """`_global/flows/` holds both sets. `post_meeting.md` is the code's flow, seeded by
    `global_seed.top_up`; `post_meeting@5.md` is a version somebody authored on top of it. The
    separator is what keeps the writer of one out of the other's namespace."""
    seeded = set(flows_pages.all_pages())
    runtime = {p["file"] for p in flows_pages.runtime_pages(
        reg, [_row("post_meeting", 5, "meeting.completed", ["process_meeting"])])}
    assert runtime == {"post_meeting@5.md"}
    assert not (runtime & seeded)
    assert flows_pages.RUNTIME_PAGE_SEP in "post_meeting@5.md"


def test_the_page_says_who_activated_it_and_when(reg):
    row = _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes"], by="admin-42")
    [page] = flows_pages.runtime_pages(reg, [row])
    assert "authored-by: admin-42" in page["body"]
    assert "status: active" in page["body"]
    assert "| **authored** | by `admin-42`, 2026-09-06 09:30Z" in page["body"]
    # And it says what a submitted flow IS, so nobody reads the appendix as something that arrived
    # over the wire: `flows_submit` never accepts code.
    assert "never as code" in page["body"]


def test_a_draft_says_it_is_a_draft(reg):
    """A flow filed and not yet activated is a thing the admin can be shown BEFORE it is live —
    which is the shape of the one confirmation the authoring ask asks for."""
    row = _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes"], status="draft")
    [page] = flows_pages.runtime_pages(reg, [row])
    assert "status: draft" in page["body"]


# ── P3 · editing is a new version, and both pages stay ──────────────────────────────────────────

def test_editing_leaves_both_pages_and_the_old_one_carries_the_retirement_line(reg):
    v1 = _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes"], status="retired")
    v2 = _row("mail_the_ops_team", 2, "meeting.completed", ["email_minutes", "email_attendees"])
    pages = {p["file"]: p for p in flows_pages.runtime_pages(reg, [v1, v2])}
    assert sorted(pages) == ["mail_the_ops_team@1.md", "mail_the_ops_team@2.md"]

    old = pages["mail_the_ops_team@1.md"]["body"]
    assert "status: retired" in old
    assert "superseded-by: 2" in old
    assert "> **Retired — version 2 is what runs now.**" in old
    # The first thing under the heading, before the prose — a reader arriving from the index has to
    # learn in the first line that this is not what happens now.
    assert old.index("Retired — version 2") < old.index("Ran when")
    assert "Ran when" in old and "Runs when" not in old
    # And the truth about work already in flight, which is the half a person gets wrong.
    assert "keeps the version it was admitted on" in old

    new = pages["mail_the_ops_team@2.md"]["body"]
    assert "status: active" in new and "superseded-by" not in new
    assert "Runs when" in new


def test_a_retirement_with_nothing_active_above_it_says_that_instead(reg):
    v1 = _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes"], status="retired")
    [page] = flows_pages.runtime_pages(reg, [v1])
    assert "> **Retired — nothing runs on this flow now.**" in page["body"]
    assert "superseded-by" not in page["body"]


def test_the_superseding_version_is_the_newest_ACTIVE_one_whatever_its_number(reg):
    """`match()` is newest-wins over active versions, so that is what the page must name — not
    simply "the next number", which would point a reader at a version that is itself retired."""
    rows = [_row("f", 1, "meeting.completed", ["email_minutes"], status="retired"),
            _row("f", 2, "meeting.completed", ["email_minutes"], status="retired"),
            _row("f", 3, "meeting.completed", ["email_minutes"])]
    pages = {p["version"]: p["body"] for p in flows_pages.runtime_pages(reg, rows)}
    assert "superseded-by: 3" in pages[1] and "superseded-by: 3" in pages[2]


# ── P4 · a step this image does not carry still renders ─────────────────────────────────────────

def test_a_row_naming_an_unknown_step_still_produces_a_page(reg):
    row = _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes", "not_a_step"])
    [page] = flows_pages.runtime_pages(reg, [row])
    assert "⚠ this flow names a step this image does not carry" in page["body"]
    assert "### 2. `not_a_step`" in page["body"]


# ── P5/P6 · the route, in its two shapes ────────────────────────────────────────────────────────

def _operator(flows_api):
    return {"X-Flows-Operator-Key": flows_api.API_KEY}


def test_the_route_serves_the_pages_of_every_runtime_version(api):
    flows_api, client = api
    _submit(flows_api, _row("mail_the_ops_team", 1, "meeting.completed", ["email_minutes"]))
    body = client.get("/flows/pages", headers=_operator(flows_api)).json()
    assert body["dir"] == "flows"
    [page] = body["pages"]
    assert page["file"] == "mail_the_ops_team@1.md"
    assert page["flow"] == "mail_the_ops_team" and page["version"] == 1
    assert page["status"] == "active"
    assert "kind: flow" in page["body"]
    assert page["etag"] == flows_pages.etag(page["body"])


def test_the_poll_shape_carries_the_etag_and_not_the_page(api):
    """The writer polls this every few seconds. A poll that had to carry every step's source would
    move `post_meeting`'s fifty kilobytes across the network to learn that nothing had changed."""
    flows_api, client = api
    _submit(flows_api, _row("mail_the_ops_team", 1, "meeting.completed", ["process_meeting"]))
    full = client.get("/flows/pages", headers=_operator(flows_api)).json()["pages"][0]
    poll = client.get("/flows/pages?bodies=0", headers=_operator(flows_api)).json()["pages"][0]
    assert "body" not in poll
    assert poll["etag"] == full["etag"] and poll["file"] == full["file"]
    assert len(full["body"]) > 2000            # the source really is the expensive half


def test_only_narrows_to_the_pages_the_writer_asked_for(api):
    flows_api, client = api
    _submit(flows_api, _row("a_flow", 1, "meeting.completed", ["email_minutes"]))
    _submit(flows_api, _row("b_flow", 1, "meeting.completed", ["email_minutes"]))
    got = client.get("/flows/pages?only=b_flow@1", headers=_operator(flows_api)).json()["pages"]
    assert [p["file"] for p in got] == ["b_flow@1.md"]
    both = client.get("/flows/pages?only=a_flow@1.md,b_flow@1",
                      headers=_operator(flows_api)).json()["pages"]
    assert [p["file"] for p in both] == ["a_flow@1.md", "b_flow@1.md"]


def test_an_image_flow_is_not_on_this_route(api):
    """It describes what somebody WROTE. The image's flows are seeded pages, and a route that
    served both would give agent-api a second, competing writer for files the seed owns."""
    flows_api, client = api
    got = client.get("/flows/pages", headers=_operator(flows_api)).json()["pages"]
    assert got == []


def test_the_route_takes_a_credential(api):
    flows_api, client = api
    assert client.get("/flows/pages").status_code == 401


def test_the_etag_is_of_the_content_not_of_the_row(reg):
    a = flows_pages.runtime_pages(reg, [_row("f", 1, "meeting.completed", ["email_minutes"])])[0]
    b = flows_pages.runtime_pages(reg, [_row("f", 1, "meeting.completed", ["email_attendees"])])[0]
    assert a["etag"] != b["etag"]
    assert a["etag"] == flows_pages.etag(a["body"])
    assert len(a["etag"]) == 16


def test_rendering_is_deterministic(reg):
    row = _row("f", 1, "meeting.completed", ["email_minutes", "drop_to_attendees"])
    assert flows_pages.runtime_pages(reg, [row]) == flows_pages.runtime_pages(reg, [row])
