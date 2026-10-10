"""An entity read or write never follows a link planted in the KG tree.

``kg/entities/<kind>/<slug>.md`` sits in a work tree the model's tools can write, and the model
supplies the entity name on an upsert. A link planted at ``kg``, ``kg/entities``, the kind directory
or the page itself must not let the root process read another tenant's entity (its bytes would reach
the model through the entity tools and the rendered INDEX) or write this workspace's entity through
the link into another tenant's tree.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from workspaces.shared import entities as e


def _victim(tmp_path):
    v = tmp_path / "victim"
    (v / "kg" / "entities" / "company").mkdir(parents=True)
    (v / "kg" / "entities" / "company" / "acme.md").write_text(
        "---\ntype: company\nid: acme\ntitle: Acme\n---\n\nVICTIM-ENTITY-BODY\n")
    return v


def test_an_upsert_does_not_write_through_a_linked_kind_dir(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    (ws / "kg" / "entities").mkdir(parents=True)
    os.symlink(v / "kg" / "entities" / "company", ws / "kg" / "entities" / "company")
    before = sorted(p.name for p in (v / "kg" / "entities" / "company").iterdir())
    # a KG directory that is a planted link is refused cleanly (not a 500, and nothing written)
    with pytest.raises(e.EntityRefused):
        e.upsert_entity(ws, "company", "Globex", facts=["founded 2001"], source="the call")
    assert sorted(p.name for p in (v / "kg" / "entities" / "company").iterdir()) == before
    assert not (v / "kg" / "entities" / "company" / "globex.md").exists()


def test_an_upsert_does_not_read_another_tenant_through_a_linked_kind_dir(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    (ws / "kg" / "entities").mkdir(parents=True)
    os.symlink(v / "kg" / "entities" / "company", ws / "kg" / "entities" / "company")
    # the workspace appears to hold no entities (the link is not followed)
    assert e.known_slugs(ws) == set()
    assert e.find_entity(ws, "Acme") is None
    idx = e.render_index(ws, "ws")
    assert "VICTIM-ENTITY-BODY" not in idx and "Acme" not in idx


def test_a_linked_page_leaf_is_replaced_not_written_through(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    (ws / "kg" / "entities" / "company").mkdir(parents=True)
    os.symlink(v / "kg" / "entities" / "company" / "acme.md",
               ws / "kg" / "entities" / "company" / "acme.md")
    e.upsert_entity(ws, "company", "Acme", facts=["new fact"], source="the call")
    assert "VICTIM-ENTITY-BODY" in (v / "kg" / "entities" / "company" / "acme.md").read_text()
    assert not (ws / "kg" / "entities" / "company" / "acme.md").is_symlink()
