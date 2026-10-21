"""D35 founder addition: a profile is a CLAIMED Person record. Strictly one-to-one; no claim without
a verified address; profile fields edited by their owner only; the public page shows only the fields
the person marked; org fields never reach a profile answer. Synthetic data only."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.caller import Caller
from crm.person_profiles import PROFILE_FIELDS, clean
from crm.persons import PersonError, PersonNotFound, PersonRefused
from crm.store import Store

ORG = 'acme'
ADMIN = Caller(subject='1', email='admin@acme.test', is_admin=True)
WORK, HOME = 'val@work.test', 'val.home@mail.test'
# signed-in people whose addresses a sign-in proved (identity.v1 email_verified)
A, B = Caller(subject='21', email=WORK, email_verified=True), Caller(subject='22', email=HOME, email_verified=True)
ORG_SECRET = 'NOTE-ONLY-THE-ORG-KNOWS-7731'


@pytest.fixture
def store():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def fk(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


def org_record(store, key='val', match=(WORK, HOME), fields=None):
    return store.create(ORG, ADMIN, 'Person', fields or {'Name': 'Val Example', 'LastContact': '2026-10-01'},
                        narrative=ORG_SECRET, reason='met', idempotency_key=key,
                        match=['email:' + m for m in match])['record_id']


def sign_in(store, caller):
    store.ensure_account(ORG, caller)
    with store.engine.begin() as c:
        store.profiles.auto_claim(c, ORG, caller)


def test_first_sign_in_claims_the_one_matching_record_and_moves_no_org_data(store):
    val = org_record(store)
    sign_in(store, A)
    mine = store.profiles.mine(ORG, A)
    assert mine['claimed'] is True and mine['record_id'] == val
    assert mine['fields'] == {}                        # nothing of the org's became the person's
    assert ORG_SECRET not in json.dumps(mine) and '2026-10-01' not in json.dumps(mine)
    # the org's record is untouched
    assert store.get(ORG, ADMIN, val)['narrative'] == ORG_SECRET


def test_one_to_one_a_second_login_joins_through_the_merge_card_not_a_second_claim(store):
    val = org_record(store)
    other = org_record(store, key='other', match=('someone@else.test',), fields={'Name': 'Other'})
    sign_in(store, A)
    sign_in(store, B)                                  # same human, second login: linked, not claimed
    assert store.profiles.mine(ORG, B)['via_merge'] is True
    with pytest.raises(PersonError) as e:
        store.profiles.claim(ORG, B, other)
    assert e.value.status in (404, 409)
    with pytest.raises(PersonError):
        store.profiles.claim(ORG, A, other)            # A already has one
    # the record itself takes one claimant: nobody else claims Val's record
    intruder = Caller(subject='40', email='someone@else.test', email_verified=True)
    with pytest.raises(PersonNotFound):
        store.profiles.claim(ORG, intruder, val)       # not a record matching their address
    with store.engine.connect() as c:
        rows = c.execute(s.profiles.select()).mappings().all()
    assert [r['claimed_by'] for r in rows if r['claimed_by']] == ['21']


def test_no_claim_without_a_verified_address(store):
    org_record(store)
    with pytest.raises(PersonError):
        store.profiles.claim(ORG, Caller(subject='21', email=''))
    with store.engine.begin() as c:
        assert store.profiles.auto_claim(c, ORG, Caller(subject='21', email='')) is None


def test_several_matches_ask_with_a_claim_card(store):
    first = org_record(store, key='a', match=(WORK,))
    second = store.create(ORG, ADMIN, 'Person', {'Name': 'V. Example', 'Email': WORK}, reason='dup',
                          idempotency_key='b')['record_id']
    sign_in(store, A)
    mine = store.profiles.mine(ORG, A)
    assert mine['claimed'] is False
    assert {c['record_id'] for c in mine['candidates']} == {first, second}
    assert set(mine['candidates'][0]) == {'record_id', 'name'}      # the name only, no org notes
    assert store.profiles.claim(ORG, A)['status'] == 'choose'
    assert store.profiles.claim(ORG, A, second) == {'status': 'claimed', 'record_id': second}


def test_profile_fields_are_edited_by_the_owner_only(store):
    val = org_record(store)
    sign_in(store, A)
    saved = store.profiles.edit(ORG, A, {'display_name': 'Val', 'headline': 'Builds things',
                                         'links': {'linkedin': 'https://www.linkedin.com/in/val-example'}})
    assert saved['fields']['display_name'] == 'Val'
    for other in (ADMIN, Caller(subject='50', email='colleague@acme.test')):
        with pytest.raises(PersonRefused):
            store.profiles.edit(ORG, other, {'bio': 'not theirs'}, record_id=val)
    with pytest.raises(PersonRefused):                 # a worker acting for her: never
        store.profiles.edit(ORG, Caller(subject='21', email=WORK, delegated=True, regime='human'), {'bio': 'x'})
    with pytest.raises(PersonError):
        clean({'links': {'linkedin': 'https://evil.example/in/val'}})
    with pytest.raises(PersonError):
        clean({'Notes': 'an org field is not a profile field'})


def test_the_public_page_is_off_by_default_and_shows_only_marked_fields(store):
    org_record(store)
    sign_in(store, A)
    store.profiles.edit(ORG, A, {'display_name': 'Val', 'bio': 'private-ish', 'location': 'Vienna'})
    assert store.profiles.mine(ORG, A)['published'] is False
    page = store.profiles.publish(ORG, A, True, ['display_name', 'location'])
    shown = store.profiles.public(page['handle'])
    assert shown['fields'] == {'display_name': 'Val', 'location': 'Vienna'}
    assert ORG_SECRET not in json.dumps(shown) and 'Val Example' not in json.dumps(shown)
    store.profiles.publish(ORG, A, False)
    assert store.profiles.public(page['handle']) is None


def test_directory_answers_profile_fields_only_and_follows_the_second_login(store):
    org_record(store)
    sign_in(store, A)
    sign_in(store, B)
    store.profiles.edit(ORG, A, {'display_name': 'Val'})
    out = store.profiles.directory(ORG, [{'subject': '21', 'email': WORK}, {'subject': '22', 'email': HOME},
                                         {'subject': '60', 'email': 'nobody@x.test'}])
    assert out['21'] == out['22'] == {'display_name': 'Val'}
    assert out['60'] is None
    assert set(out['21']) <= set(PROFILE_FIELDS)


def test_unclaim_keeps_the_record_and_returns_the_fields_to_the_org(store):
    val = org_record(store)
    sign_in(store, A)
    page = store.profiles.publish(ORG, A, True, ['display_name'])
    store.profiles.edit(ORG, A, {'display_name': 'Val'})
    assert store.profiles.unclaim(ORG, A) == {'status': 'unclaimed', 'record_id': val}
    assert store.get(ORG, ADMIN, val)['fields']['Name'] == 'Val Example'
    assert store.profiles.public(page['handle']) is None
    # the org edits them now (write access to the record), and the next sign-in does not re-claim
    assert store.profiles.edit(ORG, ADMIN, {'display_name': 'Val (org)'}, record_id=val)['status'] == 'saved'
    sign_in(store, A)
    assert store.profiles.mine(ORG, A)['claimed'] is False


def test_merge_ends_the_merged_records_claim(store):
    a = store.ensure_account(ORG, A)
    sign_in(store, A)
    sign_in(store, B)                                  # a separate sign-up: its own stub, its own claim
    store.persons.offer_for_address(ORG, A, HOME)
    card = store.persons.me(ORG, B)['merge']
    store.persons.confirm(ORG, B, card['id'])
    with store.engine.connect() as c:
        claims = [r['claimed_by'] for r in c.execute(s.profiles.select()).mappings() if r['claimed_by']]
    assert claims == ['21']
    assert store.profiles.mine(ORG, B)['record_id'] == a


def test_the_front_door_profile_routes(store):
    org_record(store)
    users = {'a': {'user_id': '21', 'email': WORK, 'email_verified': True},
             'vxd_w': {'user_id': '21', 'email': WORK, 'email_verified': True, 'delegation': {'regime': 'human', 'workspaces': []}}}
    def resolve(request):
        tok = json.loads(request.content)['token']
        return httpx.Response(200, json=users[tok]) if tok in users else httpx.Response(401)
    client = TestClient(create_app(store, Identity('http://identity', 'x', httpx.MockTransport(resolve)), ORG,
                                   internal_secret='s3-internal'))
    r = client.post('/person/profile', json={'action': 'read'}, headers={'X-API-Key': 'a'})
    assert r.status_code == 200 and r.json()['claimed'] is True          # claimed on the first request
    assert client.post('/person/profile', json={'action': 'edit', 'fields': {'display_name': 'Val'}},
                       headers={'Authorization': 'Bearer vxd_w'}).status_code == 403
    assert client.post('/person/profile', json={'action': 'edit', 'fields': {'display_name': 'Val'}},
                       headers={'X-API-Key': 'a'}).status_code == 200
    h = client.post('/person/profile', json={'action': 'publish', 'public': True, 'public_fields': ['display_name']},
                    headers={'X-API-Key': 'a'}).json()['handle']
    assert client.post('/internal/profiles/public', json={'handle': h}).status_code == 403
    pub = client.post('/internal/profiles/public', json={'handle': h}, headers={'X-Internal-Secret': 's3-internal'})
    assert pub.json() == {'handle': h, 'fields': {'display_name': 'Val'}}
    assert client.post('/person/profile', json={'action': 'import', 'source': 'linkedin'},
                       headers={'X-API-Key': 'a'}).status_code == 400              # the seam, empty


# ── retiring identity's old profile: its values move in on the claim; one writer ─────────────
def test_the_old_profile_moves_in_on_the_claim_and_the_public_page_stays_off(store):
    org_record(store)
    old = {'handle': 'H' * 22, 'published': True, 'fields': {
        'name': {'value': 'Val E.', 'public': True}, 'role': {'value': 'Founder', 'public': False},
        'links': {'value': ['https://github.com/val-example', 'https://val.example.org'], 'public': True},
        'photo': {'value': 'javascript:alert(1)', 'public': True}}}
    asked = []
    store.profiles.legacy = lambda subject: asked.append(subject) or old
    sign_in(store, A)
    rid = store.profiles.mine(ORG, A)['record_id']
    assert store.profiles.carry_legacy(ORG, '21', rid) is True
    mine = store.profiles.mine(ORG, A)
    assert mine['fields'] == {'display_name': 'Val E.', 'headline': 'Founder',
                              'links': {'github': 'https://github.com/val-example', 'website': 'https://val.example.org'}}
    assert mine['public_fields'] == ['display_name', 'links']        # its public flags, carried
    assert mine['published'] is False                                 # the open web is the person's call
    # the old handle is kept, so the old link resolves once she switches the page on
    assert store.profiles.publish(ORG, A, True)['handle'] == 'H' * 22
    assert store.profiles.public('H' * 22)['fields']['display_name'] == 'Val E.'
    # once only: a second carry does not overwrite what she has since edited
    store.profiles.edit(ORG, A, {'display_name': 'Val'})
    assert store.profiles.carry_legacy(ORG, '21', rid) is False
    assert store.profiles.mine(ORG, A)['fields']['display_name'] == 'Val'
