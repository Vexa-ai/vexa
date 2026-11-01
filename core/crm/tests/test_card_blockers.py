"""What a card still waits on (`/internal/cards/blockers`, flows' `card_blockers_done`).

A card waits on the cards in its own ``Chain.blocked_by``. ``Chain`` stores a link at both ends
(``blocks`` on the blocker, ``blocked_by`` on the card waiting) and nothing keeps them in step, so only
``blocked_by`` is read. Offline: SQLite, synthetic people. No containers.
"""
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.card_ops import Cards
from crm.store import NotFound, Store

ORG = 'acme'
ANA = Caller(subject='7', email='u7@acme.test', workspaces=('sales',), writable=('sales',))


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


@pytest.fixture
def cards(store):
    return Cards(store, clock=lambda: datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))


def make(cards, title, **fields):
    return cards.create(ORG, ANA, title=title, workspace='sales', fields=fields or None)['card']


def move(cards, card, state):
    current = cards.read(ORG, ANA, card['id'])
    return cards.update(ORG, ANA, card['id'], current['revision'], transition=state)['card']


def chain(cards, card, blocked_by=(), blocks=()):
    current = cards.read(ORG, ANA, card['id'])
    cards.update(ORG, ANA, card['id'], current['revision'],
                 fields={'Chain': {'blocked_by': list(blocked_by), 'blocks': list(blocks)}})


def test_open_done_dropped_and_missing_blockers(cards):
    doing, done, dropped, todo = (make(cards, t) for t in ('Build it', 'Design it', 'Old plan', 'Plan it'))
    move(cards, doing, 'Doing')
    move(cards, done, 'Done')
    move(cards, dropped, 'Dropped')
    waiting = make(cards, 'Ship it')
    chain(cards, waiting, blocked_by=[doing['id'], done['id'], dropped['id'], todo['id'], 'no-such-card'])
    out = cards.blockers(ORG, waiting['id'])
    assert out['card'] == waiting['id']
    assert out['blocked_by'] == [doing['id'], done['id'], dropped['id'], todo['id'], 'no-such-card']
    assert out['open'] == [{'id': doing['id'], 'state': 'Doing'}, {'id': todo['id'], 'state': 'To do'}]
    assert out['done'] == [done['id']]
    assert out['dropped'] == [{'id': dropped['id'], 'state': 'Dropped', 'title': 'Old plan'}]
    assert out['missing'] == ['no-such-card']


def test_only_blocked_by_is_read_never_blocks(cards):
    blocker, waiting = make(cards, 'Blocker'), make(cards, 'Waiting')
    # the link written only at the blocker's end (blocks) does not make the other card wait
    chain(cards, blocker, blocks=[waiting['id']])
    assert cards.blockers(ORG, waiting['id'])['blocked_by'] == []
    # and the waiting card's own blocked_by holds even when the blocker's blocks does not mirror it
    chain(cards, blocker, blocks=[])
    chain(cards, waiting, blocked_by=[blocker['id']])
    assert cards.blockers(ORG, waiting['id'])['open'] == [{'id': blocker['id'], 'state': 'To do'}]
    move(cards, blocker, 'Done')
    out = cards.blockers(ORG, waiting['id'])
    assert out['open'] == [] and out['done'] == [blocker['id']]


def test_a_card_with_no_chain_waits_on_nothing_and_an_unknown_card_is_not_found(cards):
    card = make(cards, 'Alone')
    assert cards.blockers(ORG, card['id']) == {'card': card['id'], 'blocked_by': [], 'open': [], 'done': [],
                                               'dropped': [], 'missing': []}
    with pytest.raises(NotFound):
        cards.blockers(ORG, 'no-such-card')


def test_the_route_is_internal_tier_only(store, cards):
    blocker, waiting = make(cards, 'Blocker'), make(cards, 'Waiting')
    chain(cards, waiting, blocked_by=[blocker['id']])
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    body = {'card': waiting['id']}
    assert client.post('/internal/cards/blockers', json=body).status_code == 403
    assert client.post('/internal/cards/blockers', json=body,
                       headers={'X-Internal-Secret': 'wrong'}).status_code == 403
    hdr = {'X-Internal-Secret': 'internal-s'}
    r = client.post('/internal/cards/blockers', json=body, headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()['open'] == [{'id': blocker['id'], 'state': 'To do'}]
    assert client.post('/internal/cards/blockers', json={'card': 'no-such-card'}, headers=hdr).status_code == 404
