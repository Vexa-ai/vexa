"""The one-time admin claim code (app/claim_code.py) and its issuance at boot.

Without it the first person to reach a fresh instance's sign-in screen became its administrator.
Offline: the module is pure, and issuance runs against a small in-memory session double that answers
the three things it touches (the claim lock, "is there an admin", the admin_claim row).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from admin_api.app import claim_code
from admin_api.app.main import issue_admin_claim_code

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def test_a_code_is_sixteen_unambiguous_characters_in_four_groups():
    code = claim_code.generate()
    groups = code.split("-")
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)
    assert set("".join(groups)) <= set(claim_code.ALPHABET)
    assert len({claim_code.generate() for _ in range(50)}) == 50


def test_typing_is_forgiving_and_only_the_digest_is_kept():
    code = "ABCD-EF01-JKMN-PQRS"
    rec = claim_code.record(code, host="h", now=NOW)
    assert code not in str(rec) and code.replace("-", "") not in str(rec)
    for typed in ("abcd ef01 jkmn pqrs", "ABCDEFO1JKMNPQRS", "  abcd-efol-jkmn-pqrs "):
        assert claim_code.matches(typed, rec), typed
    for wrong in ("", None, "ABCD-EF01-JKMN-PQRT", "ABCD-EF01-JKMN", "x" * 400):
        assert not claim_code.matches(wrong, rec), wrong


def test_a_consumed_record_opens_nothing():
    assert not claim_code.matches("ABCD-EF01-JKMN-PQRS", {})
    assert not claim_code.matches("ABCD-EF01-JKMN-PQRS", None)


def test_a_fresh_code_from_another_replica_is_kept_and_an_old_one_is_not():
    rec = claim_code.record("ABCD-EF01-JKMN-PQRS", host="pod-a", now=NOW)
    assert claim_code.held_elsewhere(rec, host="pod-b", now=NOW + timedelta(seconds=30)) == "pod-a"
    assert claim_code.held_elsewhere(rec, host="pod-a", now=NOW + timedelta(seconds=30)) is None
    assert claim_code.held_elsewhere(rec, host="pod-b", now=NOW + timedelta(hours=1)) is None


def test_the_announcement_names_the_code_and_the_alternative():
    text = claim_code.announcement("ABCD-EF01-JKMN-PQRS")
    assert "ABCD-EF01-JKMN-PQRS" in text and "VEXA_ADMIN_EMAILS" in text and "once" in text


# ── issuance ─────────────────────────────────────────────────────────────────────────────────────

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class Session:
    def __init__(self, *, admin=False, row=None):
        self.admin = admin
        self.rows = {}
        if row is not None:
            self.rows[claim_code.ROW_KEY] = SimpleNamespace(key=claim_code.ROW_KEY, value=row)
        self.locked = False
        self.commits = 0

    async def execute(self, stmt, params=None):
        if not hasattr(stmt, "selected_columns"):
            self.locked = True
            return _Rows([])
        return _Rows([(1,)] if self.admin else [])

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self):
        self.commits += 1

    def value(self):
        row = self.rows.get(claim_code.ROW_KEY)
        return row.value if row else None


def _issue(db, host="pod-a", now=NOW, force=False):
    return asyncio.run(issue_admin_claim_code(db, host=host, now=now, force=force))


@pytest.fixture(autouse=True)
def _no_admin_list(monkeypatch):
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)


def test_an_unclaimed_instance_gets_a_code_under_the_claim_lock():
    db = Session()
    code, note = _issue(db)
    assert code and note == "issued" and db.locked and db.commits == 1
    assert claim_code.matches(code, db.value())


def test_a_restart_issues_a_new_code_and_retires_the_old_one():
    db = Session()
    first, _ = _issue(db)
    second, _ = _issue(db, now=NOW + timedelta(seconds=5))
    assert second and second != first
    assert claim_code.matches(second, db.value()) and not claim_code.matches(first, db.value())


def test_a_replica_starting_alongside_keeps_the_code_another_one_just_issued():
    db = Session()
    first, _ = _issue(db, host="pod-a")
    code, note = _issue(db, host="pod-b", now=NOW + timedelta(seconds=10))
    assert code is None and "pod-a" in note
    assert claim_code.matches(first, db.value())
    # …unless the role was explicitly released, which always asks for a new one
    forced, _ = _issue(db, host="pod-b", now=NOW + timedelta(seconds=20), force=True)
    assert forced and claim_code.matches(forced, db.value())


def test_no_code_once_an_admin_exists_and_a_stale_one_is_retired():
    db = Session(admin=True, row=claim_code.record("ABCD-EF01-JKMN-PQRS", host="x", now=NOW))
    code, note = _issue(db)
    assert code is None and "claimed" in note
    assert not claim_code.is_live(db.value())


def test_no_code_while_the_deployment_names_the_admins(monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    db = Session(row=claim_code.record("ABCD-EF01-JKMN-PQRS", host="x", now=NOW))
    code, note = _issue(db)
    assert code is None and "VEXA_ADMIN_EMAILS" in note
    assert not claim_code.is_live(db.value())
