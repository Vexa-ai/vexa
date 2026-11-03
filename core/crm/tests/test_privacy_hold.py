"""D6 / D20: CRM answers are labelled private, and a marked worker's CRM write or card proposal is held.

The edge forwards the session's mark as `X-Vexa-Taint`. Only `none` lets a worker write into a
surface other people read; a source list, `unverifiable` or no header at all is held (fail closed).
A person's own credential is never held.
"""
from __future__ import annotations

import pytest

from test_cards_api import client_for, store  # noqa: F401  (fixture)


def _h(token, taint=None):
    h = {'Authorization': f'Bearer {token}', 'x-trace-id': 'trace-' + token}
    if taint is not None:
        h['x-vexa-taint'] = taint
    return h


PROPOSAL = {'title': 'Chase Acme for 615', 'workspace': 'sales', 'reason': 'mail said so'}


def test_every_crm_answer_is_labelled_private(store):
    client = client_for(store)
    r = client.post('/card/list', json={}, headers=_h('me'))
    assert r.headers['x-vexa-privacy'] == 'private; class=crm'


@pytest.mark.parametrize('taint', ['mail', 'crm,transcript', 'unverifiable', None])
def test_a_marked_or_unknown_worker_card_proposal_is_held(store, taint):
    client = client_for(store)
    r = client.post('/card/propose', json=PROPOSAL, headers=_h('vxd_watched', taint))
    assert r.status_code == 409, r.text
    d = r.json()['detail']
    assert d['refused'] == 'private_sources_to_shared' and d['fault']['code'] == 'held_private_egress'
    assert d['surface'] == 'card_proposal'


def test_an_unmarked_worker_proposes(store):
    client = client_for(store)
    assert client.post('/card/propose', json=PROPOSAL, headers=_h('vxd_watched', 'none')).status_code == 200


def test_a_proposal_onto_the_persons_own_desktop_is_not_held(store):
    client = client_for(store)
    r = client.post('/card/propose', json={'title': 'Chase Acme', 'reason': 'mine'}, headers=_h('vxd_watched', 'mail'))
    assert r.status_code == 200, r.text


def test_a_marked_worker_crm_write_is_held(store):
    client = client_for(store)
    r = client.post('/change', json={'action': 'create', 'object_type': 'company', 'fields': {'Name': 'Acme'},
                                     'reason': 'from mail', 'idempotency_key': 'k1'},
                    headers=_h('vxd_watched', 'mail'))
    assert r.status_code == 409, r.text
    assert r.json()['detail']['surface'] == 'crm_write'


def test_a_person_is_never_held(store):
    client = client_for(store)
    r = client.post('/card/create', json={'title': 'Renewal', 'workspace': 'sales'}, headers=_h('me', 'mail'))
    assert r.status_code == 200, r.text
