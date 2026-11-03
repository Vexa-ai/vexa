"""Entity hygiene from a real workspace (2026-10-14): a project page listed four people twice in
`## Connected` after parallel writes (one chip by name, one by `ws:` id), and a company page written
at `kg/entities/<name>.md` was neither in its kind's folder nor in `kg/INDEX.md`.

One chip per page however it is spelled; a stray page with a kind is filed into its folder; every
page under `kg/entities/` is in the index; and two parallel upserts of linked pages leave one chip
each.
"""
from __future__ import annotations

import subprocess
import threading

from workspaces.shared import entities as E


def _ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_one_chip_per_page_whatever_its_spelling():
    card = E.parse_card("# Meetup\n\n## Connected\n\n- [[Val Example]] — organiser\n"
                        "- [[Ada Example]]\n- [[ws:abc123/val-example]] — organiser\n"
                        "- [[Ada Example]] — speaker\n- [[Val Example]]\n")
    assert E.dedupe_connected(card)
    chips = [l for l in card.content("Connected") if l.startswith("- ")]
    assert chips == ["- [[Val Example]] — organiser", "- [[Ada Example]] — speaker"]
    assert not E.dedupe_connected(card)          # idempotent


def test_an_upsert_cleans_duplicates_already_on_the_page(tmp_path):
    root = _ws(tmp_path)
    E.upsert_entity(root, "project", "Meetup", ["kick-off"], "call")
    page = root / "kg/entities/project/meetup.md"
    page.write_text(page.read_text() + "\n## Connected\n\n- [[Ada Example]]\n- [[Ada Example]]\n")
    E.upsert_entity(root, "project", "Meetup", ["venue booked"], "call")
    assert page.read_text().count("[[Ada Example]]") == 1


def test_a_stray_page_with_a_kind_is_filed_and_indexed(tmp_path):
    root = _ws(tmp_path)
    (root / "kg/entities").mkdir(parents=True)
    (root / "kg/entities/ci-vendor.md").write_text("---\ntype: company\ntitle: CI Vendor\n---\n\n# CI Vendor\n")
    (root / "kg/entities/loose-note.md").write_text("# A loose note\n")
    moved = E.file_strays(root)
    assert moved == [("kg/entities/ci-vendor.md", "kg/entities/company/ci-vendor.md")]
    assert (root / "kg/entities/company/ci-vendor.md").is_file()
    assert not (root / "kg/entities/ci-vendor.md").exists()
    index = E.render_index(root, "ws")
    assert "kg/entities/company/ci-vendor.md" in index
    # a page with no kind stays where it is, and is listed as unfiled rather than missing
    assert "| unfiled | loose-note | `kg/entities/loose-note.md`" in index


def test_parallel_upserts_through_the_route_leave_one_chip_each(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings
    from tests.test_entity_connections import _FakeIdentity, _FakeRuntime

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(ws_root))
    client = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                                   reader=WorkspaceReader(str(ws_root))))
    h = {"X-User-Id": "u_jane"}
    client.post("/api/workspace/entity", json={"kind": "project", "name": "Meetup", "facts": ["start"],
                                               "source": "call"}, headers=h)
    people = [f"Person {i}" for i in range(4)]
    for p in people:
        client.post("/api/workspace/entity", json={"kind": "person", "name": p, "facts": ["met"],
                                                   "source": "call"}, headers=h)

    def link(p):
        client.post("/api/workspace/entity", json={
            "kind": "person", "name": p, "facts": [f"{p} joins"], "source": "call",
            "connections": [{"name": "Meetup", "relation": "organises"}]}, headers=h)

    threads = [threading.Thread(target=link, args=(p,)) for p in people * 2]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = next(ws_root.rglob("kg/entities/project/meetup.md")).read_text()
    for p in people:
        assert text.count(f"[[{p}]]") == 1, text
