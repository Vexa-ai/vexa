"""A Person record is claimed, matched or attached only by an address a sign-in proved.

Identity answers `/internal/validate` with `email_verified` (identity.v1): true only when a sign-in
proved the person holds `email` — an emailed link, or a provider that vouched for it. An address an
API call set, or an identity provider merely asserted, is `false` (or missing, from an older
identity). Such a caller claims no record, is offered none on the claim card, and is not attached to
the record that holds the address. Callers here are built the way crm-api builds them, from identity's
answer (`caller.from_validate`). Synthetic data only."""
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.bootstrap import bootstrap_org
from crm.caller import Caller, from_validate
from crm.persons import PersonError
from crm.store import Store

ORG = 'acme'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
VAL = 'val@work.test'


def identity_answer(uid, email, **extra):
    return {'user_id': uid, 'scopes': ['bot', 'tx', 'browser'], 'max_concurrent': 1, 'email': email,
            'is_admin': False, 'admin_account': False, 'credential_class': 'session', **extra}


def caller(uid=21, email=VAL, **extra):
    return from_validate('session-token', identity_answer(uid, email, **extra))


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def fk(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def org_record(store, key='val', match=True):
    return store.create(ORG, ADMIN, 'Person', {'Name': 'Val Example', 'Email': VAL}, narrative='org note',
                        reason='met', idempotency_key=key, match=['email:' + VAL] if match else [])['record_id']


def sign_in(store, who):
    store.ensure_account(ORG, who)
    with store.engine.begin() as c:
        return store.profiles.auto_claim(c, ORG, who)


def claims(store):
    with store.engine.connect() as c:
        return [r['claimed_by'] for r in c.execute(s.profiles.select()).mappings().all() if r['claimed_by']]


def account_of(store, record_id):
    with store.engine.connect() as c:
        return c.execute(select(s.records.c.account_id).where(s.records.c.org_id == ORG,
                                                              s.records.c.id == record_id)).scalar_one()


@pytest.mark.parametrize('stated', [{}, {'email_verified': False}, {'email_verified': 'true'}],
                         ids=['missing', 'false', 'not-a-boolean'])
def test_sign_in_with_an_unproven_address_claims_nothing(store, stated):
    org_record(store)
    who = caller(**stated)
    assert sign_in(store, who) is None
    assert claims(store) == []
    assert store.profiles.mine(ORG, who)['claimed'] is False


def test_an_unproven_address_is_refused_the_claim_card(store):
    val = org_record(store)
    org_record(store, key='dup', match=False)         # two matches: the card would list them
    who = caller()
    sign_in(store, who)
    assert store.profiles.mine(ORG, who)['candidates'] == []
    with pytest.raises(PersonError) as refused:
        store.profiles.claim(ORG, who, val)
    assert refused.value.status == 403
    assert claims(store) == []


def test_an_unproven_address_attaches_no_record_and_becomes_no_identifier(store):
    val = org_record(store)
    who = caller(uid=31, email='someone@else.test')
    mine = store.ensure_account(ORG, who)
    forged = caller(uid=32)                           # asserts VAL's address, unproven
    created = store.ensure_account(ORG, forged)
    assert account_of(store, val) is None             # VAL's record stays unattached
    assert created != val
    fields = store.get(ORG, ADMIN, created)['fields']
    assert 'Email' not in fields
    with store.engine.connect() as c:
        held = c.execute(select(s.match_ids.c.record_id).where(
            s.match_ids.c.org_id == ORG, s.match_ids.c.identifier == 'email:' + VAL)).scalar_one()
    assert held == val                                # the identifier is still the org's record's
    assert mine is not None


def test_a_proven_address_claims_the_one_matching_record(store):
    val = org_record(store)
    who = caller(email_verified=True)
    assert sign_in(store, who) == val
    assert claims(store) == ['21']
    assert account_of(store, val) == '21'


def test_proving_the_address_later_claims_on_that_sign_in(store):
    val = org_record(store)
    assert sign_in(store, caller()) is None
    assert sign_in(store, caller(email_verified=True)) == val
    assert claims(store) == ['21']


def test_identity_must_say_true_for_the_address_to_count():
    assert caller(email_verified=True).email_verified is True
    for stated in ({}, {'email_verified': False}, {'email_verified': 'true'}, {'email_verified': 1}):
        assert caller(**stated).email_verified is False
    assert Caller(subject='21', email=VAL).email_verified is False      # a bare subject proves nothing
