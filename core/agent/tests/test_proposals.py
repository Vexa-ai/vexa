"""THE SHORT LIST an empty chat offers — the store and its three doors (Vexa-ai/vexa#1614).

Founder, 2026-09-06, on the new-chat empty state: *"that is a short list that is updated by other
agents when they see something as JTBD, can have up to 10 items"*.

Five properties, and four of them are about the list not lying:

  1. FOUR FIELDS, AND `since` IS WHEN IT WAS FIRST SEEN — not when a flow last re-ran.
  2. DEDUP IS source + act. A second write of the same job updates the row in place.
  3. A CLOSED ITEM STAYS CLOSED. The tombstone is why: a re-run must not re-offer the thing the
     person just dismissed, which is the one failure that would make the row untrustworthy for good.
  4. TEN OPEN, NEWEST FIRST. The eleventh pushes out the oldest OPEN one, never a closed one.
  5. IT IS GIT-EXCLUDED. A queue is not a fact about the workspace, and committing it would put a
     new version of the file in somebody's history every time an agent noticed something.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared import proposals as store


# ── the store ────────────────────────────────────────────────────────────────────────────────────

def test_an_item_carries_its_source_its_act_its_since_and_its_status(tmp_path):
    row = store.add(tmp_path, source="meeting:97", act="The migration doc",
                    source_label="Pilot sync", by="post-meeting", at=1_700_000_000.0)
    assert row["added"] is True
    assert row["source"] == "meeting:97"
    assert row["act"] == "The migration doc"
    assert row["since"] == "2023-11-14T22:13:20Z"
    assert row["status"] == "open"
    assert row["by"] == "post-meeting"
    assert store.open_items(tmp_path) == [{k: v for k, v in row.items() if k != "added"}]


def test_the_id_is_the_dedup_key_so_a_writer_can_name_a_row_it_has_not_read(tmp_path):
    row = store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert row["id"] == store.item_id("meeting:97", "The migration doc")
    assert row["id"] != store.item_id("meeting:98", "The migration doc")


def test_a_duplicate_updates_in_place_and_keeps_the_since_it_already_had(tmp_path):
    first = store.add(tmp_path, source="meeting:97", act="The migration doc",
                      source_label="Pilot sync", at=1_700_000_000.0)
    again = store.add(tmp_path, source="meeting:97", act="The migration doc",
                      source_label="Pilot sync (rescheduled)", at=1_700_090_000.0)
    assert again["added"] is False
    assert again["since"] == first["since"]          # the job is as old as the FIRST sighting
    assert again["source_label"] == "Pilot sync (rescheduled)"
    assert len(store.open_items(tmp_path)) == 1


@pytest.mark.parametrize("status", ["ran", "dismissed"])
def test_a_closed_item_leaves_the_list_and_a_rerun_does_not_bring_it_back(tmp_path, status):
    row = store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert store.resolve(tmp_path, row["id"], status)["status"] == status
    assert store.open_items(tmp_path) == []
    store.add(tmp_path, source="meeting:97", act="The migration doc")   # the flow ran again
    assert store.open_items(tmp_path) == []


def test_resolving_something_that_is_not_there_answers_none_rather_than_inventing_a_row(tmp_path):
    store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert store.resolve(tmp_path, "deadbeef", "dismissed") is None


def test_a_row_only_ever_closes_as_ran_or_dismissed(tmp_path):
    row = store.add(tmp_path, source="meeting:97", act="x y z")
    with pytest.raises(ValueError):
        store.resolve(tmp_path, row["id"], "open")


def test_ten_open_newest_first_and_the_eleventh_pushes_out_the_oldest(tmp_path):
    for n in range(12):
        store.add(tmp_path, source=f"meeting:{n}", act=f"Job {n}", at=1_700_000_000.0 + n)
    acts = [i["act"] for i in store.open_items(tmp_path)]
    assert acts == [f"Job {n}" for n in range(11, 1, -1)]      # 11 down to 2 — ten of them
    assert len(acts) == store.OPEN_MAX


def test_the_cap_drops_open_rows_never_the_tombstones_that_stop_a_resurrection(tmp_path):
    dismissed = store.add(tmp_path, source="meeting:0", act="Job 0")
    store.resolve(tmp_path, dismissed["id"], "dismissed")
    for n in range(1, 13):
        store.add(tmp_path, source=f"meeting:{n}", act=f"Job {n}")
    store.add(tmp_path, source="meeting:0", act="Job 0")
    assert [i["act"] for i in store.open_items(tmp_path)].count("Job 0") == 0


def test_a_proposal_needs_both_halves_of_its_identity(tmp_path):
    with pytest.raises(ValueError):
        store.add(tmp_path, source="", act="The migration doc")
    with pytest.raises(ValueError):
        store.add(tmp_path, source="meeting:97", act="   ")


def test_an_unreadable_file_is_an_empty_list_never_an_error(tmp_path):
    (tmp_path / ".vexa").mkdir()
    (tmp_path / store.PROPOSALS_FILE).write_text("{not json", encoding="utf-8")
    assert store.read(tmp_path) == []
    assert store.open_items(tmp_path) == []
    # …and the next write repairs it rather than inheriting the damage.
    store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert json.loads((tmp_path / store.PROPOSALS_FILE).read_text())["contract"] == "proposals.v1"


def test_the_queue_is_excluded_from_the_desks_history(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert f"/{store.PROPOSALS_FILE}" in (tmp_path / ".git" / "info" / "exclude").read_text()


def test_a_desk_that_is_not_a_repository_still_gets_its_list(tmp_path):
    store.add(tmp_path, source="meeting:97", act="The migration doc")
    assert len(store.open_items(tmp_path)) == 1
    assert not (tmp_path / ".git").exists()


# ── the doors ────────────────────────────────────────────────────────────────────────────────────

class _Reader:
    """The one method the router uses of the workspace registry."""

    def __init__(self, root):
        self.root = root

    def workspace_dir(self, subject: str):
        if not subject:
            raise ValueError("invalid subject")
        return self.root / subject


@pytest.fixture()
def client(tmp_path):
    from control_plane.routers import proposals as router_mod

    def subject_of(request):
        return request.headers.get("x-user-id") or ""

    app = FastAPI()
    app.include_router(router_mod.build(subject_of=subject_of, wsr=_Reader(tmp_path)))
    return TestClient(app)


HEAD = {"X-User-Id": "7"}


def test_the_read_is_a_file_read_behind_an_identity_and_a_new_desk_is_simply_empty(client):
    r = client.get("/api/proposals", headers=HEAD)
    assert r.status_code == 200
    assert r.json() == {"items": [], "max": store.OPEN_MAX}


def test_an_agent_proposes_and_the_person_reads_it_back(client):
    made = client.post("/api/proposals", headers=HEAD, json={
        "source": "meeting:97", "act": "The migration doc",
        "source_label": "Pilot sync", "by": "post-meeting"})
    assert made.status_code == 201 and made.json()["added"] is True
    items = client.get("/api/proposals", headers=HEAD).json()["items"]
    assert [(i["act"], i["source_label"], i["by"]) for i in items] \
        == [("The migration doc", "Pilot sync", "post-meeting")]


def test_the_list_is_the_callers_own_never_a_desk_named_in_the_body(client):
    client.post("/api/proposals", headers=HEAD, json={"source": "meeting:97", "act": "Mine"})
    assert client.get("/api/proposals", headers={"X-User-Id": "8"}).json()["items"] == []


def test_a_proposal_with_half_an_identity_is_refused(client):
    assert client.post("/api/proposals", headers=HEAD, json={"act": "no source"}).status_code == 400


def test_the_row_leaves_on_run_and_on_dismiss(client):
    for status in ("ran", "dismissed"):
        made = client.post("/api/proposals", headers=HEAD,
                           json={"source": f"meeting:{status}", "act": "Do the thing"}).json()
        assert client.post("/api/proposals/resolve", headers=HEAD,
                           json={"id": made["id"], "status": status}).json()["status"] == status
    assert client.get("/api/proposals", headers=HEAD).json()["items"] == []


def test_resolving_a_row_this_desk_does_not_have_is_a_404(client):
    assert client.post("/api/proposals/resolve", headers=HEAD,
                       json={"id": "deadbeef", "status": "ran"}).status_code == 404


def test_resolve_needs_an_id_and_a_status_it_recognises(client):
    made = client.post("/api/proposals", headers=HEAD,
                       json={"source": "meeting:97", "act": "Do the thing"}).json()
    assert client.post("/api/proposals/resolve", headers=HEAD,
                       json={"status": "ran"}).status_code == 400
    assert client.post("/api/proposals/resolve", headers=HEAD,
                       json={"id": made["id"], "status": "snoozed"}).status_code == 400
