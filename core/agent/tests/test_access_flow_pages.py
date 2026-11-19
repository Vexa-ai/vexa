"""Access vectors for the per-person flow-page mirror (ADR-0046 decision 10): a scope's uid names
one desk directly under the workspaces root, and nothing else."""
from __future__ import annotations

import pytest

from control_plane import flow_pages_watch as watch


@pytest.mark.parametrize("scope", ["user:..", "user:.", "user:a/../b", "user:..x", "user:.hidden"])
def test_a_uid_that_is_not_one_plain_name_is_not_a_scope(monkeypatch, scope):
    monkeypatch.setattr(watch, "_get", lambda path: {"flows": [{"scope": scope},
                                                                {"scope": "user:42"}]})
    assert watch.user_scopes() == ["user:42"]


def test_the_mirror_writes_only_into_a_desk_directly_under_the_root(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    (root / "42").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "evil").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(watch, "user_scopes", lambda: ["user:42", "user:evil", "user:.."])
    seen: list = []

    def fake_reconcile(desk, **kw):
        seen.append(desk)
        return []
    monkeypatch.setattr(watch, "reconcile", fake_reconcile)
    watch.reconcile_people(root)
    assert seen == [root / "42"]
