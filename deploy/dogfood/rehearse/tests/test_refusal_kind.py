'''A correct refusal is not a defect, and a malformed subject is the one account the address
guard cannot reach.

Both come from the same live run. `blank-admin` refused correctly on a claimed instance and was
filed as a `blocker` (`fr_af253789b2780003`) — a non-problem at the top of the fixer's dump. And
user 131 (`20260902t183213z`) was minted by `ensure_user` from a mis-parsed ICS, which
`subject_reset` cannot clean because there is no domain to judge.
'''
from __future__ import annotations

import pytest

from rehearse import catalogue as cat
from rehearse.doors import DoorRefused
from rehearse.engine import (Refused, is_malformed, rehearse, subject_reset,
                             subject_reset_malformed)
from rehearse.stub_doors import StubDoors
from rehearse import run_all as ra


# ── a precondition that says no ─────────────────────────────────────────────────────────────────

def test_the_precondition_verbs_are_declared_not_guessed_from_their_names():
    assert cat.VERBS['require_instance_blank'].precondition
    assert cat.VERBS['require_subject_absent'].precondition
    assert not cat.VERBS['user_ensure'].precondition
    assert not cat.VERBS['await_mail'].precondition


def test_a_refused_precondition_marks_the_result_refused_not_merely_failed(catalog, env):
    res = rehearse('blank-admin', 'admin@rehearse.test', doors=StubDoors(blank=False),
                   catalog=catalog, env=env)
    assert res.ok is False and res.refused is True
    assert res.to_dict()['refused'] is True


def test_a_door_that_says_no_is_NOT_a_refusal(catalog, env):
    '''The distinction has to hold both ways, or it is just a second word for failure.'''
    class NoMail(StubDoors):
        def await_mail(self, to, subject_contains='', budget_s=180, since=0.0):
            raise DoorRefused('no mail arrived')
    res = rehearse('organizer-invited', 'x@rehearse.test', doors=NoMail(), catalog=catalog,
                   env=env)
    assert res.ok is False and res.refused is False


def test_run_all_files_a_refusal_as_kind_refused_and_not_a_blocker(catalog, env, tmp_path,
                                                                   monkeypatch):
    monkeypatch.setattr(ra, 'FRICTION_FALLBACK', tmp_path / 'f.jsonl')
    filed = []
    report = ra.run(StubDoors(blank=False), catalog=catalog, env=env, only=['blank-admin'],
                    reporter=filed.append)
    rec = filed[0]
    assert rec['kind'] == 'refused'
    assert rec['severity'] != 'blocker'
    assert 'PRECONDITION, not a defect' in rec['what_went_wrong']


def test_a_refusal_is_counted_apart_from_a_failure(catalog, env, tmp_path, monkeypatch):
    '''`failed` is what somebody has to fix. Counting a precondition there makes the number mean
    something other than what a reader assumes.'''
    monkeypatch.setattr(ra, 'FRICTION_FALLBACK', tmp_path / 'f.jsonl')
    report = ra.run(StubDoors(blank=False), catalog=catalog, env=env, only=['blank-admin'])
    assert report['failed'] == []
    assert report['refused'] == ['blank-admin']
    assert 'SKIP' in ra.render(report)
    assert 'precondition, not a defect' in ra.render(report)


# ── the malformed subject ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('email,malformed', [
    ('20260902t183213z', True),          # THE ONE — user 131, an invite's DTSTAMP
    ('', True),
    ('someone@localhost', True),
    ('a b@rehearse.test', True),
    ('dmitry@vexa.ai', False),
    ('rehearse-group-member@rehearse.test', False),
])
def test_malformed_means_no_person_could_be_named_by_it(email, malformed):
    assert is_malformed(email) is malformed


def test_a_malformed_account_is_reset_by_id():
    doors = StubDoors()
    doors.users['20260902t183213z'] = '131'
    doors.desks['131'] = ['README.md']
    out = subject_reset_malformed('131', doors=doors)
    assert out['ok'], out['remaining']
    assert out['by'] == 'id'
    assert doors.user_find('20260902t183213z') is None


@pytest.mark.parametrize('email', ['dmitry@vexa.ai', 'rehearse-x@rehearse.test'])
def test_a_WELL_FORMED_address_is_refused_by_the_by_id_path(email):
    '''The bound that makes this safe: a real person's address is well-formed, so this path can
    never reach one. It is a narrower rule, not a domain exemption.'''
    doors = StubDoors()
    doors.users[email] = '126'
    doors.desks['126'] = ['README.md']
    with pytest.raises(Refused, match='well-formed address'):
        subject_reset_malformed('126', doors=doors)
    assert doors.user_find(email) == '126' and doors.desks['126']


def test_subject_reset_still_refuses_the_malformed_address_by_name():
    '''The address path is unchanged — it cannot judge a value with no domain, so it says no.'''
    doors = StubDoors()
    doors.users['20260902t183213z'] = '131'
    with pytest.raises(Refused):
        subject_reset('20260902t183213z', doors=doors)


def test_both_paths_run_the_SAME_removal_sequence():
    '''One rulebook: two spellings of a reset is how the second one ends up weaker.'''
    import inspect
    from rehearse import engine
    for fn in (engine.subject_reset, engine.subject_reset_malformed):
        assert '_reset_stores' in inspect.getsource(fn)
