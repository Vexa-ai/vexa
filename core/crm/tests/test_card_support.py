"""Support cards (founder 2 Nov ~09:00Z): an inbound support message makes ONE card per thread (no Kind,
Waiting=support, the SLA's Due, About = the sender's Person), our reply moves it to Done, a newer message
reopens it. Only the internal tier writes it, as ``flows:support``; the route takes no other field. Offline: SQLite."""
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.card_ops import Cards
from crm.store import Store

ORG = 'acme'
ADMIN = Caller(subject='1', email='a@acme.test', workspaces=('sales',), writable=('sales',), is_admin=True)
THREAD = 'https://discord.com/channels/1/2/300'


@pytest.fixture
def st():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def _person(st, name, mail, stage):
    co = st.create(ORG, ADMIN, 'Company', {'Name': name + ' Co', 'Stage': stage}, idempotency_key='c' + mail)['record_id']
    return st.create(ORG, ADMIN, 'Person', {'Name': name, 'Email': mail, 'Company': co},
                     match=['email:' + mail], idempotency_key='p' + mail)['record_id']


def test_one_card_per_thread_done_on_our_reply_and_reopened_by_a_newer_message(st):
    cs = Cards(st)
    pid = _person(st, 'Bailey', 'bailey@impel.test', 'Customer')
    one = cs.support_inbound(ORG, THREAD, 'Bots do not join', 'Bailey <bailey@impel.test>', '2026-11-02T09:00:00Z', '2026-11-02')
    assert one['status'] == 'created' and one['customer'] is True
    card = cs.due_cards(ORG, one['card'])['cards'][0]
    assert (card['owner'], card['due'], card['state']) == ('support', '2026-11-02', 'To do')
    two = cs.support_inbound(ORG, THREAD + '/', 'more', 'Bailey <bailey@impel.test>', '2026-11-02T09:05:00Z', '2026-11-02')
    assert (two['card'], two['status']) == (one['card'], 'open')                    # the same thread: the same card
    assert cs.support_reply(ORG, THREAD, '2026-11-02T10:00:00Z')['status'] == 'done'
    assert cs.due_cards(ORG, one['card'])['cards'][0]['state'] == 'Done'
    assert cs.support_reply(ORG, THREAD, '2026-11-02T10:01:00Z')['status'] == 'unchanged'
    assert cs.support_inbound(ORG, THREAD, 'x', '', '2000-01-01T00:00:00Z', None)['status'] == 'unchanged'  # older
    again = cs.support_inbound(ORG, THREAD, 'x', '', '2099-11-03T08:00:00Z', '2099-11-03')
    assert (again['card'], again['status']) == (one['card'], 'reopened')
    assert cs.due_cards(ORG, one['card'])['cards'][0]['due'] == '2099-11-03'
    with st.engine.connect() as c:
        fields = c.execute(s.records.select().where(s.records.c.id == one['card'])).first().fields
        actors = {a for (a,) in c.execute(s.revisions.select().with_only_columns(s.revisions.c.actor_id)
                                           .where(s.revisions.c.record_id == one['card']))}
    assert fields['About'] == 'crm:' + pid and 'Kind' not in fields and actors == {'flows:support'}


def test_a_sender_who_is_not_on_a_customer_company_makes_no_card_when_only_customers_count(st):
    cs = Cards(st)
    _person(st, 'Pat', 'pat@prospect.test', 'User')
    assert cs.support_inbound(ORG, 'https://mail.google.com/mail/u/0/#all/t1', 'hi', 'Pat <pat@prospect.test>',
                              '2026-11-02T09:00:00Z', '2026-11-03', customers_only=True) == {'card': None, 'status': 'not_a_customer'}
    assert cs.support_inbound(ORG, 'https://mail.google.com/mail/u/0/#all/t2', 'hi', 'x@nobody.test',
                              '2026-11-02T09:00:00Z', '2026-11-03', customers_only=True)['card'] is None


def test_the_route_is_internal_only_and_takes_no_other_field(st):
    client = TestClient(create_app(st, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    body = {'source': THREAD, 'title': 'q', 'sender': '', 'at': '2026-11-02T09:00:00Z', 'due': '2026-11-02'}
    for path in ('/internal/cards/support/inbound', '/internal/cards/support/reply'):
        b = body if path.endswith('inbound') else {'source': THREAD, 'at': body['at']}
        assert client.post(path, json=b).status_code == 403
        assert client.post(path, json=b, headers={'Authorization': 'Bearer a-person-token'}).status_code in (401, 403)
    h = {'X-Internal-Secret': 'internal-s'}
    for extra in ({'kind': 'software'}, {'Kind': 'question'}, {'waiting': 'ceo'}, {'state': 'Done'}, {'about': 'crm:x'}):
        assert client.post('/internal/cards/support/inbound', json={**body, **extra}, headers=h).status_code == 422
    r = client.post('/internal/cards/support/inbound', json=body, headers=h)
    assert r.status_code == 200 and r.json()['status'] == 'created'



def test_the_heartbeat_reads_open_cards_with_a_mark_that_moves_with_a_new_fact_next_or_state(st):
    cs = Cards(st)
    one = cs.support_inbound(ORG, THREAD, 'q', '', '2026-11-02T09:00:00Z', '2026-11-02')
    (a,) = cs.beat_cards(ORG, one['card'])['cards']
    assert a['owner'] == 'support' and a['open'] is True and a['every_s'] == 86400 and a['next_check_at']
    assert cs.beat_cards(ORG, one['card'])['cards'][0]['mark'] == a['mark']             # nothing new: the same mark
    cs.support_reply(ORG, THREAD, '2026-11-02T10:00:00Z')
    (b,) = cs.beat_cards(ORG, one['card'])['cards']
    assert b['open'] is False and b['mark'] != a['mark']
    assert one['card'] not in [c['id'] for c in cs.beat_cards(ORG)['cards']]            # closed: not among the open
