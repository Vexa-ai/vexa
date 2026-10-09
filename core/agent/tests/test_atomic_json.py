"""The one atomic JSON write: a reader sees the old document or the new one, never half of either."""
from __future__ import annotations

import json
import os
import stat

import pytest

from shared.atomic_json import write_json_atomic


def test_the_document_lands_whole_and_private(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"b": 1, "a": [1, 2]}, indent=2, sort_keys=True)
    assert path.read_text() == json.dumps({"b": 1, "a": [1, 2]}, indent=2, sort_keys=True)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    write_json_atomic(path, {"c": 3})
    assert json.loads(path.read_text()) == {"c": 3}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_failed_write_leaves_the_old_document_and_no_temporary(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"ok": True})
    with pytest.raises(TypeError):
        write_json_atomic(path, {"not": object()})
    assert json.loads(path.read_text()) == {"ok": True}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
