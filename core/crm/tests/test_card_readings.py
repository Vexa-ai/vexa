"""Sensor readings on a card (`readings.py`, `/internal/records/reading`, `/internal/cards/by-source`).

A card that tracks a GitHub issue or pull request names it in its typed ``Source``; the reading the
system makes there is written as the card's ``Reading`` — the declared fields with evidence — by the
internal route only. Offline: SQLite, synthetic people. No containers.
"""
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import readings as rd
from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import DEFINITIONS, bootstrap_org, upgrade_card_definition
from crm.caller import Caller
from crm.card_ops import Cards
from crm.store import CRMError, NotFound, Store

ORG = 'acme'
ANA = Caller(subject='7', email='u7@acme.test', workspaces=('sales',), writable=('sales',))
AGENT = Caller(subject='7', workspaces=('sales',), writable=('sales',), delegated=True, regime='autonomous',
               session='s1', credential_class='delegation')
PR = 'https://github.com/Vexa-ai/vexa/pull/1962'
SOURCE = 'github/vexa-pulls'
READ = {'title': 'Per-account ramp limit beside the concurrency ceiling', 'state': 'closed',
        'merged_at': '2026-10-31T18:02:11Z', 'head_sha': 'f916fc8efa9d2bb11c4ac9d089830c1407764a8a',
        'labels': [{'name': 'state: merged', 'color': 'x'}, 'area: bots'], 'url': PR,
        'updated_at': '2026-10-31T18:02:12Z'}
EV = {'read_at': 1_793_500_000.0, 'query': '/repos/Vexa-ai/vexa/pulls?state=all', 'cursor': '2026-10-31T00:00:00Z'}


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


def revisions(store, record):
    with store.engine.connect() as c:
        return c.execute(select(s.revisions.c.revision, s.revisions.c.actor_id, s.revisions.c.evidence)
                         .where(s.revisions.c.org_id == ORG, s.revisions.c.record_id == record)
                         .order_by(s.revisions.c.revision)).all()


def test_subject_names_an_issue_and_a_pull_request_the_same_way():
    want = 'github.com/vexa-ai/vexa#1962'
    for link in (PR, 'https://github.com/Vexa-ai/vexa/issues/1962', 'https://github.com/Vexa-AI/Vexa/pull/1962/files',
                 'Vexa-ai/vexa#1962', 'github:Vexa-ai/vexa#1962'):
        assert rd.subject(link) == want, link
    assert rd.summary_subject('Tracks it. GitHub: Vexa-ai/vexa#1962 (merge card)') == want
    assert rd.subject('https://linear.app/x/issue/A-1/') == 'https://linear.app/x/issue/A-1'
    assert rd.subject('not a link') == '' and rd.subject('http://github.com/a/b/pull/1') == ''


def test_a_reading_writes_the_declared_fields_with_evidence_and_the_card_shows_it(cards, store):
    card = make(cards, 'Scheduled bots guaranteed on time (#1962)', Source=PR, Next='review it')
    out = cards.write_reading(ORG, card['id'], SOURCE, READ, EV)
    assert out['status'] == 'applied' and out['revision'] == card['revision'] + 1
    got = cards.read(ORG, ANA, card['id'])
    assert got['title'] == 'Scheduled bots guaranteed on time (#1962)'          # the typed Title is untouched
    assert got['next'] == 'review it' and got['source'] == PR
    assert got['reading']['fields'] == {
        'title': 'Per-account ramp limit beside the concurrency ceiling', 'state': 'merged',   # closed + merged_at
        'merged_at': '2026-10-31T18:02:11Z', 'head_sha': 'f916fc8efa9d2bb11c4ac9d089830c1407764a8a',
        'labels': ['state: merged', 'area: bots'], 'url': PR, 'updated_at': '2026-10-31T18:02:12Z'}
    assert got['reading']['evidence'] == {'source': SOURCE, **EV}
    last = revisions(store, card['id'])[-1]
    assert last.actor_id == 'reading:' + SOURCE and last.evidence == [{'reading': {'source': SOURCE, **EV}}]


def test_idempotent_by_record_source_and_read_at_and_a_later_reading_replaces(cards, store):
    card = make(cards, 'Track it', Source=PR)
    assert cards.write_reading(ORG, card['id'], SOURCE, READ, EV)['status'] == 'applied'
    n = len(revisions(store, card['id']))
    # the same reading again (a retry, a redelivery): nothing changes
    assert cards.write_reading(ORG, card['id'], SOURCE, {**READ, 'title': 'other'}, EV)['status'] == 'unchanged'
    # a later reading with the same values: no revision, the earlier evidence stands
    assert cards.write_reading(ORG, card['id'], SOURCE, READ, {**EV, 'read_at': EV['read_at'] + 60})['status'] == 'unchanged'
    assert len(revisions(store, card['id'])) == n
    # an older reading never overwrites a newer one
    older = cards.write_reading(ORG, card['id'], SOURCE, {'title': 'old', 'state': 'open'}, {'read_at': 1_000.0})
    assert older['status'] == 'stale'
    # a later reading replaces the earlier one whole
    later = {'title': 'Renamed at the source', 'state': 'open', 'labels': []}
    assert cards.write_reading(ORG, card['id'], SOURCE, later, {**EV, 'read_at': EV['read_at'] + 120})['status'] == 'applied'
    assert cards.read(ORG, ANA, card['id'])['reading']['fields'] == later
    assert len(revisions(store, card['id'])) == n + 1


def test_only_declared_fields_in_their_declared_shape(cards):
    card = make(cards, 'Track it', Source=PR)
    for bad in ({'Title': 'agent field'}, {'Next': 'x'}, {'title': 'ok', 'body': 'prose'}, {},
                {'state': 'reopened'}, {'head_sha': 'not-a-sha'}, {'merged_at': 'yesterday'},
                {'url': 'http://x'}, {'title': 'x' * 301}):
        with pytest.raises(CRMError):
            cards.write_reading(ORG, card['id'], SOURCE, bad, EV)
    for source, ev in (('nope', EV), (SOURCE, {}), (SOURCE, {'read_at': 'now'}), (SOURCE, {'read_at': True})):
        with pytest.raises(CRMError):
            cards.write_reading(ORG, card['id'], source, READ, ev)
    with pytest.raises(NotFound):
        cards.write_reading(ORG, 'no-such-card', SOURCE, READ, EV)
    assert cards.read(ORG, ANA, card['id'])['reading'] is None


def test_no_person_agent_or_generic_write_reaches_the_reading(cards, store):
    card = make(cards, 'Track it', Source=PR)
    fake = {'fields': {'title': 'typed by hand'}, 'evidence': {'source': 'x/y', 'read_at': 1.0}}
    with pytest.raises(CRMError):
        cards.update(ORG, ANA, card['id'], card['revision'], fields={'Reading': fake})
    with pytest.raises(CRMError):
        store.change(ORG, ANA, card['id'], card['revision'], {'Reading': fake})
    with pytest.raises(CRMError):
        store.change(ORG, AGENT, card['id'], card['revision'], {'Reading': fake}, reason='r', propose=True)
    with pytest.raises(CRMError):
        cards.create(ORG, ANA, title='Born read', workspace='sales', fields={'Reading': fake})
    assert cards.read(ORG, ANA, card['id'])['reading'] is None


def test_source_is_a_typed_link(cards):
    card = make(cards, 'Track it')
    got = cards.update(ORG, ANA, card['id'], card['revision'], fields={'Source': 'Vexa-ai/vexa#1987'})['card']
    assert got['source'] == 'Vexa-ai/vexa#1987'
    with pytest.raises(CRMError):
        cards.update(ORG, ANA, card['id'], got['revision'], fields={'Source': 'just words'})
    assert {'Source', 'Reading'} <= set(DEFINITIONS['Card']['properties'])


def test_by_source_finds_the_typed_link_and_the_legacy_summary(cards):
    typed = make(cards, 'Typed', Source=PR)
    legacy = make(cards, 'Legacy', Summary='GitHub: Vexa-ai/vexa#1962 · the merge card')
    other = make(cards, 'Other', Source='https://github.com/Vexa-ai/vexa/pull/1987')
    # a typed Source wins over the Summary: this one tracks #1987 although its summary names #1962
    make(cards, 'Typed elsewhere', Source='Vexa-ai/vexa#1987', Summary='GitHub: Vexa-ai/vexa#1962')
    out = cards.by_source(ORG, 'https://github.com/Vexa-ai/vexa/issues/1962')
    assert out['subject'] == 'github.com/vexa-ai/vexa#1962'
    assert sorted(out['cards']) == sorted([typed['id'], legacy['id']])
    assert other['id'] in cards.by_source(ORG, 'Vexa-ai/vexa#1987')['cards']
    assert cards.by_source(ORG, 'Vexa-ai/vexa#1')['cards'] == []
    with pytest.raises(CRMError):
        cards.by_source(ORG, 'not a link')


def test_an_existing_org_gains_source_and_reading(store):
    from crm.bootstrap import CARD_LIFECYCLE_PROPERTIES
    assert {'Source', 'Reading'} <= set(CARD_LIFECYCLE_PROPERTIES)
    assert upgrade_card_definition(store.engine, ORG) is False      # a fresh org already has them


def test_the_routes_are_internal_tier_only(store, cards):
    card = make(cards, 'Track it', Source=PR)
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    hdr = {'X-Internal-Secret': 'internal-s'}
    body = {'record': card['id'], 'source': SOURCE, 'fields': READ, 'evidence': EV}
    for path, b in (('/internal/records/reading', body), ('/internal/cards/by-source', {'link': PR})):
        assert client.post(path, json=b).status_code == 403
        assert client.post(path, json=b, headers={'X-Internal-Secret': 'wrong'}).status_code == 403
    r = client.post('/internal/cards/by-source', json={'link': PR}, headers=hdr)
    assert r.status_code == 200 and r.json()['cards'] == [card['id']]
    r = client.post('/internal/records/reading', json=body, headers=hdr)
    assert r.status_code == 200 and r.json()['status'] == 'applied', r.text
    assert client.post('/internal/records/reading', json=body, headers=hdr).json()['status'] == 'unchanged'
    bad = client.post('/internal/records/reading', json={**body, 'fields': {'Title': 'x'}}, headers=hdr)
    assert 400 <= bad.status_code < 500
    assert client.post('/internal/records/reading', json={**body, 'record': 'nope'}, headers=hdr).status_code == 404



# ── readings land on people and companies (founder 1 Nov 2026, L2.7) ───────────────────────

def test_a_reading_by_email_lands_on_the_matched_person_only_through_the_reading_route():
    import json as _json
    from crm import schema as _s
    from crm.bootstrap import bootstrap_org
    from crm.card_ops import Cards
    from crm.store import CRMError as _E, Store
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    _s.metadata.create_all(engine)
    bootstrap_org(engine, 'acme', 'Acme')
    st = Store(engine)
    cs = Cards(st)
    from crm.caller import Caller
    admin = Caller(subject='1', email='a@acme.test', workspaces=('sales',), writable=('sales',), is_admin=True)
    pid = st.create('acme', admin, 'Person', {'Name': 'Ann', 'Email': 'ann@globex.test'},
                    match=['email:ann@globex.test'], idempotency_key='p1')['record_id']
    got = cs.by_source('acme', 'ann@globex.test')
    assert got['cards'] == [pid] and got['subject'] == 'email:ann@globex.test'
    out = cs.write_reading('acme', pid, 'posthog/signup', {'title': 'signup'},
                           {'read_at': 1793560000.0, 'query': 'posthog signup'})
    assert out['status'] == 'applied'
    with engine.connect() as c:
        rec = st._row(c, 'acme', pid)
    assert rec['fields']['Reading']['fields']['title'] == 'signup'
    rev = st.history('acme', admin, pid)['revisions'][0]
    assert rev['actor_id'] == 'reading:posthog/signup'
    with pytest.raises(_E):                                          # nobody writes Reading by hand
        st.change('acme', admin, pid, rec['revision'], {'Reading': None}, reason='x')



def test_a_mail_header_names_its_email():
    assert rd.email_of("Ann Lee <Ann@Globex.test>") == "ann@globex.test"
    assert rd.email_of("email:ann@globex.test") == "ann@globex.test"
    assert rd.email_of("https://github.com/x/y/pull/1") == "" and rd.email_of("Ann <not-an-email>") == ""
