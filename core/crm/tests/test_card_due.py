"""``/internal/cards/due``: the cards flows' ``card_due_alarms`` reads (PRD L3.2). Every open card with a
``Due`` answers its id, the date, its owner (``Waiting``: whose stream the alarm goes to) and ``open``; one
named card answers whatever it holds, so a cleared Due or a closed card cancels its alarm. Internal tier
only. Offline: SQLite."""
import httpx
from fastapi.testclient import TestClient

from crm.api import Identity, create_app

from test_card_stages import ANA, ORG, cards, software, store  # noqa: F401 — fixtures


def _card(cards, title, **fields):  # noqa: F811
    return cards.create(ORG, ANA, title=title, workspace='sales', fields=fields)['card']


def test_open_cards_with_a_due_answer_due_and_owner_and_a_named_card_answers_always(store, cards):  # noqa: F811
    due = _card(cards, 'Send the deck', Due='2026-11-09', Waiting='ceo')
    none = _card(cards, 'No date', Waiting='ceo')
    soft = software(cards, Due='2026-11-12', Waiting='software')
    done = _card(cards, 'Finished', Due='2026-11-03', Waiting='business')
    for state in ('Doing', 'Done'):
        rev = cards.due_cards(ORG, done['id'])['cards'][0]['revision']
        cards.update(ORG, ANA, done['id'], rev, fields={'State': state})
    got = {c['id']: c for c in cards.due_cards(ORG)['cards']}
    assert set(got) == {due['id'], soft['id']}
    assert {k: got[due['id']][k] for k in ('due', 'owner', 'open')} == {'due': '2026-11-09', 'owner': 'ceo', 'open': True}
    assert {k: got[soft['id']][k] for k in ('due', 'owner')} == {'due': '2026-11-12', 'owner': 'software'}
    (one,) = cards.due_cards(ORG, done['id'])['cards']
    assert one['open'] is False and one['due'] == '2026-11-03' and one['state'] == 'Done'
    (bare,) = cards.due_cards(ORG, none['id'])['cards']
    assert bare['due'] is None and bare['open'] is True
    assert cards.due_cards(ORG, 'no-such-card')['cards'] == []


def test_the_due_route_is_internal_tier_only(store, cards):  # noqa: F811
    _card(cards, 'Send the deck', Due='2026-11-09', Waiting='ceo')
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    assert client.post('/internal/cards/due', json={}).status_code == 403
    assert client.post('/internal/cards/due', json={}, headers={'X-Internal-Secret': 'wrong'}).status_code == 403
    assert client.post('/internal/cards/due', json={},
                       headers={'Authorization': 'Bearer a-person-token'}).status_code in (401, 403)
    r = client.post('/internal/cards/due', json={}, headers={'X-Internal-Secret': 'internal-s'})
    assert r.status_code == 200 and [c['owner'] for c in r.json()['cards']] == ['ceo']



def test_a_card_change_names_the_watched_fields_its_state_and_both_owners(store, cards):  # noqa: F811
    card = _card(cards, 'Send the deck', Waiting='software')
    new = cards.card_change(ORG, card['id'], card['revision'])
    assert new['created'] is True and 'Waiting' in new['changed'] and new['owner'] == 'software'
    after = cards.update(ORG, ANA, card['id'], card['revision'], fields={'Due': '2026-11-09', 'Waiting': 'ceo'})
    rev = after['card']['revision'] if 'card' in after else after['revision']
    ch = cards.card_change(ORG, card['id'], rev)
    assert ch['changed'] == ['Due', 'Waiting'] and (ch['owner'], ch['was_owner']) == ('ceo', 'software')
    assert ch['actor'] == ANA.subject and ch['open'] is True and ch['created'] is False
    assert cards.card_change(ORG, card['id'], 999) == {'card': None}
    assert cards.card_change(ORG, 'no-such-card', 1) == {'card': None}
