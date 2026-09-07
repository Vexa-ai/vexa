"""`writeback_prompt` names the target workspace (ledger F196/F198/F200, live agent, 2026-09-03).

Live repro: a turn ran entirely inside the shared workspace `zenith-c172ae` — `workspace_write`
calls in the same turn wrote there directly — and the write-back phase that followed still called
`entity_upsert` with no `slug`, landing three pages on the personal desk instead. The phase's own
prompt (`writeback_prompt`) never told the model the shared workspace's slug existed to pass; the
model had no way to know `entity_upsert` needed one.
"""
from __future__ import annotations

from worker import engine


DESK = {"slug": "seed", "path": "/w/seed", "write": True, "primary": True, "role": "private"}
ZENITH = {"slug": "zenith-c172ae", "path": "/w/zenith-c172ae", "write": True, "primary": False,
          "role": "shared", "name": "Zenith FINOS"}
BRAIN_TRUST = {"slug": "brain-trust-ab12", "path": "/w/brain-trust-ab12", "write": True,
              "primary": False, "role": "shared"}
VIEWER_ONLY = {"slug": "readonly-ws", "path": "/w/readonly-ws", "write": False, "primary": False,
              "role": "shared"}


def test_exactly_one_shared_workspace_is_named_with_its_slug():
    p = engine.writeback_prompt(["Brain Trust"], [DESK, ZENITH])
    assert 'slug="zenith-c172ae"' in p, p
    assert "zenith-c172ae" in p, p


def test_no_shared_workspace_says_nothing_about_slugs():
    """The personal desk is already the right default with no shared workspace active — nothing to
    correct, so the prompt should not grow a workspace paragraph that names nothing."""
    p = engine.writeback_prompt(["Brain Trust"], [DESK])
    assert "slug=" not in p, p


def test_multiple_shared_workspaces_are_all_named_rather_than_guessed():
    p = engine.writeback_prompt(["Brain Trust"], [DESK, ZENITH, BRAIN_TRUST])
    assert "zenith-c172ae" in p, p
    assert "brain-trust-ab12" in p, p
    # Never pick one for the model — that would be exactly the guess this fix removes.
    assert 'slug="zenith-c172ae"' not in p, p


def test_a_read_only_mount_is_never_offered_as_a_write_target():
    p = engine.writeback_prompt(["Brain Trust"], [DESK, VIEWER_ONLY])
    assert "readonly-ws" not in p, p


def test_active_mounts_from_the_real_dispatch_env_threads_through(monkeypatch):
    """`active_mounts()` reads `VEXA_MOUNTS` — the same source the dispatch itself sees — and
    `writeback_prompt` must be ABLE to take that verbatim, since that is exactly what the call
    site now passes (`writeback_prompt(candidates, active_mounts())`)."""
    import json as _json
    monkeypatch.setenv("VEXA_MOUNTS", _json.dumps([DESK, ZENITH]))
    prompt = engine.writeback_prompt(["Brain Trust"], engine.active_mounts())
    assert "zenith-c172ae" in prompt, prompt
