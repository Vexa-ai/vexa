"""Security review 17.B1: a read finds a Person only by an address a sign-in PROVED.

An account whose address no sign-in proved (an API-made account naming someone else's address) must
not read that person's claimed profile (``mine``), see their Person (``me``), get their profile fields
in the directory, be grouped into their Person (``/internal/persons``) or open a merge card from it.
All data is synthetic (``*.test`` addresses, numeric account ids)."""
from test_person_claim_verified import ADMIN, ORG, VAL, caller, sign_in, store  # noqa: F401

ALIAS = 'val@personal.test'


def _val_with_a_private_profile(store):
    rid = store.create(ORG, ADMIN, 'Person', {'Name': 'Val', 'Email': VAL}, narrative='n', reason='met',
                       idempotency_key='v', match=['email:' + VAL, 'email:' + ALIAS])['record_id']
    val = caller(uid=21, email_verified=True)
    assert sign_in(store, val) == rid
    store.profiles.edit(ORG, val, {'location': 'SECRET-LOCATION', 'bio': 'private bio'})
    return rid


def test_an_unproven_address_reads_nobodys_profile_or_person(store):
    rid = _val_with_a_private_profile(store)
    mallory = caller(uid=99, email=ALIAS)
    assert mallory.email_verified is False
    store.ensure_account(ORG, mallory)
    mine = store.profiles.mine(ORG, mallory)
    assert mine['claimed'] is False and mine['fields'] == {} and mine['record_id'] is None
    me = store.persons.me(ORG, mallory)
    assert me['person'] != rid
    assert store.profiles.directory(ORG, [{'subject': '99', 'email': ALIAS}])['99'] is None
    grouped = store.persons.group(ORG, [{'subject': '99', 'email': ALIAS}])['99']
    assert grouped['person'] != rid and grouped['accounts'] == ['99']
    store.persons.offer_for_address(ORG, mallory, VAL)
    assert all(c['status'] != 'offered' or '99' not in ((c['patch'] or {}).get('accounts') or [])
               for c in _cards(store, rid))


def test_the_same_address_proven_still_finds_the_person(store):
    rid = _val_with_a_private_profile(store)
    second = caller(uid=22, email=ALIAS, email_verified=True)
    store.ensure_account(ORG, second)
    mine = store.profiles.mine(ORG, second)
    assert mine['claimed'] is True and mine['record_id'] == rid and mine.get('via_merge')
    assert store.persons.me(ORG, second)['person'] == rid
    # once an account signed in with a proven address it is linked: its subject alone finds the Person
    assert store.persons.group(ORG, [{'subject': '22', 'email': ''}])['22']['person'] == rid
    assert store.profiles.directory(ORG, [{'subject': '22'}])['22']['location'] == 'SECRET-LOCATION'
    # and a producer that vouches for the address groups an account crm has not seen yet
    assert store.persons.group(ORG, [{'subject': '23', 'email': ALIAS, 'email_verified': True}])['23']['person'] == rid


def _cards(store, rid):
    from sqlalchemy import select
    from crm import schema as s
    p = s.change_proposals
    with store.engine.connect() as c:
        return [dict(r) for r in c.execute(select(p).where(p.c.org_id == ORG, p.c.record_id == rid)).mappings()]
