"""`core/flows/scripts/import_legacy_friction_redis.py` — the one-shot Redis-to-flows migration
for #1510's C5 (the retired agent-api `FrictionStore`). Offline: `_legacy_records` (the only redis
touchpoint) is monkeypatched, so this never needs a real redis or a real flows database, and
`--apply` is never exercised end to end here (deliberately: the migration itself is a manual,
one-time, human-run act against a live deployment, not a thing CI performs)."""
from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / "scripts"
         / "import_legacy_friction_redis.py")


def _load():
    spec = importlib.util.spec_from_file_location("import_legacy_friction_redis", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_script_exists_and_loads():
    assert SCRIPT.is_file()
    assert hasattr(_load(), "main")


def test_refs_for_reported_carries_the_original_id_and_an_empty_session_verbatim():
    """The founder's own 13 reports from the 2026-09-03 live call had no session at all -- this
    migration must not invent one for history nobody can verify any more."""
    mod = _load()
    rec = {"id": "fr_legacy1", "subject": "126", "session": "", "tried": "x", "happened": "y",
          "severity": "blocker", "context": {"tool": "bot_say", "meeting_id": "104"}}
    refs = mod._refs_for_reported(rec)
    assert refs == {"uid": "126", "session": "", "friction_id": "fr_legacy1",
                    "what_i_tried": "x", "what_happened": "y", "severity": "blocker",
                    "meeting_id": "104", "tool": "bot_say"}


def test_refs_for_fixed_falls_back_when_no_fix_ref_was_recorded():
    mod = _load()
    assert mod._refs_for_fixed({"id": "fr_1", "fix_ref": ""}) == {
        "friction_id": "fr_1", "fix_ref": "(migrated, no fix_ref recorded)"}
    assert mod._refs_for_fixed({"id": "fr_1", "fix_ref": "PR #1409"}) == {
        "friction_id": "fr_1", "fix_ref": "PR #1409"}


def test_plan_admits_fixed_for_fixed_and_recurring_but_not_open():
    mod = _load()
    records = [
        {"id": "fr_open", "status": "open", "tried": "a", "happened": "b"},
        {"id": "fr_fixed", "status": "fixed", "tried": "a", "happened": "b", "fix_ref": "sha1"},
        {"id": "fr_recurring", "status": "recurring", "tried": "a", "happened": "b",
         "fix_ref": "sha2"},
    ]
    plan = mod._plan(records)
    by_id = {p["friction_id"]: p for p in plan}
    assert by_id["fr_open"]["fixed_refs"] is None
    assert by_id["fr_fixed"]["fixed_refs"] == {"friction_id": "fr_fixed", "fix_ref": "sha1"}
    assert by_id["fr_recurring"]["fixed_refs"] == {"friction_id": "fr_recurring", "fix_ref": "sha2"}


def test_a_record_with_no_id_is_skipped_rather_than_admitted_blind():
    mod = _load()
    assert mod._plan([{"status": "open", "tried": "a", "happened": "b"}]) == []


def test_main_defaults_to_dry_run_and_touches_no_database(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "_legacy_records",
                        lambda url: [{"id": "fr_1", "subject": "126", "session": "s1",
                                     "tried": "a", "happened": "b", "status": "open"}])
    called = []
    monkeypatch.setattr(mod, "_apply", lambda *a, **k: called.append(True))
    # main() parses argv directly; call it via sys.argv so the dry-run default is exercised
    import sys
    old_argv = sys.argv
    sys.argv = ["import_legacy_friction_redis.py", "--redis-url", "redis://x/0"]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0
    assert called == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "fr_1" in out


def test_apply_without_yes_is_refused(monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "_legacy_records", lambda url: [])
    called = []
    monkeypatch.setattr(mod, "_apply", lambda *a, **k: called.append(True))
    import sys
    old_argv = sys.argv
    sys.argv = ["import_legacy_friction_redis.py", "--redis-url", "redis://x/0", "--apply"]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 1 and called == []


def test_apply_with_yes_but_no_db_url_is_refused(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_legacy_records", lambda url: [])
    called = []
    monkeypatch.setattr(mod, "_apply", lambda *a, **k: called.append(True))
    import sys
    old_argv = sys.argv
    sys.argv = ["import_legacy_friction_redis.py", "--redis-url", "redis://x/0",
               "--apply", "--yes"]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 1 and called == []


def test_apply_with_yes_and_db_url_calls_apply_with_the_plan(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_legacy_records",
                        lambda url: [{"id": "fr_1", "subject": "126", "session": "s1",
                                     "tried": "a", "happened": "b", "status": "open"}])
    called = {}
    monkeypatch.setattr(mod, "_apply", lambda plan, db_url: called.update(plan=plan, db_url=db_url))
    import sys
    old_argv = sys.argv
    sys.argv = ["import_legacy_friction_redis.py", "--redis-url", "redis://x/0",
               "--flows-db-url", "postgresql://flows/flows", "--apply", "--yes"]
    try:
        rc = mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0
    assert called["db_url"] == "postgresql://flows/flows"
    assert called["plan"][0]["friction_id"] == "fr_1"
