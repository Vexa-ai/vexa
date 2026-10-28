"""A workspace's identity (``.vexa/workspace.json``), its touch log (``.vexa/touches.json``) and its
``PURPOSE`` are never read or written through a link.

All three sit in a work tree the model's tools can write. The identity id flows into every
cross-workspace link and the desk README; the purpose is returned to the caller and folded into the
dispatch preamble. A link planted at ``.vexa``, ``PURPOSE`` or the file itself must not redirect a
root read to another file, nor a root write through the link.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from workspaces.shared import workspace_id as wid
from control_plane import workspace_purpose as wpurpose

ID = "abcdefghijklmnopqrstuvwxyz234567"[: wid.ID_LEN]


def _victim(tmp_path):
    v = tmp_path / "victim"
    (v / ".vexa").mkdir(parents=True)
    (v / ".vexa" / "workspace.json").write_text(json.dumps({"id": ID, "kind": "company", "created": "x"}))
    (v / ".vexa" / "touches.json").write_text(json.dumps([{"workspace": "w", "path": "p", "at": 1}]))
    (v / "PURPOSE").write_text("victim purpose\n")
    return v


def test_identity_is_not_read_through_a_linked_vexa(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    os.symlink(v / ".vexa", ws / ".vexa")
    assert wid.read_workspace_json(ws) is None
    assert wid.workspace_id_of(ws) is None
    assert wid.read_touches(ws) == []


def test_identity_is_not_written_through_a_linked_vexa(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    os.symlink(v / ".vexa", ws / ".vexa")
    before = (v / ".vexa" / "workspace.json").read_text()
    rec, minted = wid.ensure_workspace_json(ws, kind="desk", created="now")
    assert (v / ".vexa" / "workspace.json").read_text() == before   # victim untouched
    assert not (ws / ".vexa").is_symlink()                          # the link was replaced
    assert minted and wid.workspace_id_of(ws) == rec["id"] and rec["id"] != ID


def test_a_linked_workspace_json_leaf_is_replaced_not_written_through(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    (ws / ".vexa").mkdir(parents=True)
    os.symlink(v / ".vexa" / "workspace.json", ws / ".vexa" / "workspace.json")
    wid.write_workspace_json(ws, id=ID, kind="desk", created="now")
    assert json.loads((v / ".vexa" / "workspace.json").read_text())["kind"] == "company"  # untouched
    assert not (ws / ".vexa" / "workspace.json").is_symlink()


def test_purpose_is_not_read_or_written_through_a_link(tmp_path):
    v = _victim(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    os.symlink(v / "PURPOSE", ws / "PURPOSE")
    assert wpurpose.read_purpose(ws) == ""                          # not read through the link
    wpurpose.write_purpose(ws, "my purpose")
    assert (v / "PURPOSE").read_text() == "victim purpose\n"        # victim untouched
    assert not (ws / "PURPOSE").is_symlink() and wpurpose.read_purpose(ws) == "my purpose"
