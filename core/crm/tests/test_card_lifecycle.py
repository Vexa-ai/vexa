"""S5 (PRD 0.13.4 D11–D14, D8, H9): a card of a kind is an obligation with one next actor, a
seeded lifecycle template, two-phase handoffs and decision records.

Offline: SQLite, synthetic people (D = 3 waits, V = 17 does, X = 40 outsider). No containers.
"""
import dataclasses
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import CARD_LIFECYCLE_PROPERTIES, DEFINITIONS, bootstrap_org, upgrade_card_definition
from crm.caller import Caller
from crm.card_ops import Cards
from crm.store import CRMError, Conflict, Forbidden, Refused, Store

ORG = 'acme'


class Clock:
    def __init__(self):
        self.at = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.at


@pytest.fixture
def clock():
    return Clock()


def _engine():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)

    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, c):
        dbapi.execute('PRAGMA foreign_keys=ON')
    s.metadata.create_all(engine)
    return engine


@pytest.fixture
def store():
    engine = _engine()
    bootstrap_org(engine, ORG, 'Acme')
    return Store(engine)


@pytest.fixture
def cards(store, clock):
    return Cards(store, clock=clock)


def person(uid, *ws, session=True):
    return Caller(subject=uid, email=f'u{uid}@acme.test', workspaces=tuple(ws), writable=tuple(ws),
                  credential_class='session' if session else 'key')


D, V, X = person('3', 'team'), person('17', 'team'), person('40', 'team')
ROOM_AGENT = Caller(subject='900', workspaces=('team',), writable=('team',), delegated=True, regime='autonomous',
                    shared=True, session='room-1', extra={'on_behalf_of': '3', 'principal_kind': 'workspace',
                                                          'principal_workspace': 'team'})
D_AGENT = Caller(subject='3', workspaces=('team',), writable=('team',), delegated=True, regime='human',
                 session='pchat-3')


def offer(cards, kind='commitment', next_actor='17', requester='3', title='Send the venue contract', **fields):
    out = cards.propose(ORG, D_AGENT, title=title, workspace='team', reason='promised on the call',
                        fields={'Kind': kind, 'NextActor': next_actor, 'Requester': requester,
                                'Origin': 'session:room-1', 'Due': '2026-10-20', **fields})
    assert out['status'] == 'offered', out
    return out['card']


def decide(cards, who, card_id, action, **kw):
    n = cards.nonce(ORG, who, card_id)
    return cards.decide(ORG, who, card_id, action, n['nonce'], n['content_hash'], **kw)


def _outbox(store, record_id):
    with store.engine.connect() as c:
        return [r['event'] for r in c.execute(select(s.outbox).where(s.outbox.c.record_id == record_id)
                                               .order_by(s.outbox.c.revision)).mappings()]


# ── D11: the card carries kind, one next actor, a next check, a template ─────────────────────
def test_an_agent_offers_a_commitment_and_only_the_next_actor_can_move_it(cards):
    card = offer(cards)
    assert card['kind'] == 'commitment' and card['state'] == 'proposed'
    assert card['states'] == ['proposed', 'accepted', 'delivered', 'confirmed', 'closed']
    assert card['next_actor'] == '17' and card['requester'] == '3' and card['origin'] == 'session:room-1'
    assert card['handoff']['phase'] == 'offered' and card['next_check_at']
    assert {m['subject'] for m in card['members']} == {'3', '17'}
    v = cards.read(ORG, V, card['id'])
    assert v['viewer'] == {'role': 'next_actor', 'seen': False, 'actions': ['acknowledge', 'deliver', 'decline']}
    assert cards.read(ORG, D, card['id'])['viewer']['actions'] == []           # D waits; it is not D's move
    with pytest.raises(Forbidden):
        decide(cards, D, card['id'], 'acknowledge')


def test_a_kind_card_needs_a_next_actor_and_never_changes_kind(cards):
    with pytest.raises(CRMError):
        cards.create(ORG, D, title='Floating', fields={'Kind': 'commitment'})
    card = cards.create(ORG, D, title='Ask V', workspace='team', fields={'Kind': 'question', 'NextActor': '17'})['card']
    assert card['requester'] == '3' and card['state'] == 'asked'
    with pytest.raises(CRMError):
        cards.update(ORG, D, card['id'], card['revision'], fields={'Kind': 'approval'})


def test_a_kind_card_moves_only_through_its_gates(cards, store):
    card = offer(cards)
    with pytest.raises(CRMError):                                   # a member's stage edit is refused
        cards.update(ORG, V, card['id'], card['revision'], transition='accepted')
    with pytest.raises(CRMError):                                   # crm_change too
        store.change(ORG, V, card['id'], card['revision'], {'State': 'closed'}, reason='x')
    with pytest.raises(CRMError):                                   # gate-only fields are never edited
        cards.update(ORG, V, card['id'], card['revision'], fields={'Handoff': 'acknowledged'})
    with pytest.raises(CRMError):
        store.change(ORG, V, card['id'], card['revision'], {'Handoff': 'acknowledged'}, reason='x')


def test_lifecycle_templates_are_seeded_policy_the_org_may_rename(store, clock):
    renamed = Cards(store, clock=clock, policy_text=lambda: (
        '---\ncard_templates.commitment: Promised > Taken > Handed over > Signed off > Done | No | Lapsed\n---\n'))
    out = renamed.propose(ORG, D_AGENT, title='T', reason='r', workspace='team',
                          fields={'Kind': 'commitment', 'NextActor': '17'})['card']
    assert out['state'] == 'Promised' and out['states'][-1] == 'Done'
    n = renamed.nonce(ORG, V, out['id'])
    after = renamed.decide(ORG, V, out['id'], 'acknowledge', n['nonce'], n['content_hash'])['card']
    assert after['state'] == 'Taken'


# ── D12: taken is not passed ───────────────────────────────────────────────────────────────────
def test_custody_moves_only_on_acknowledgement_and_a_gate_passes_on_its_owner(cards):
    card = offer(cards)
    acked = decide(cards, V, card['id'], 'acknowledge')['card']
    assert acked['state'] == 'accepted' and acked['handoff']['phase'] == 'acknowledged'
    assert acked['next_actor'] == '17' and acked['last_decision']['action'] == 'acknowledge'
    with pytest.raises(CRMError):                                   # delivering cites evidence
        decide(cards, V, card['id'], 'deliver')
    delivered = decide(cards, V, card['id'], 'deliver', evidence=['page:team/venue.md'])['card']
    assert delivered['state'] == 'delivered' and delivered['next_actor'] == '3'
    assert delivered['handoff']['phase'] == 'offered'                # offered back to the person waiting
    with pytest.raises(Forbidden):                                   # V cannot confirm their own delivery
        decide(cards, V, card['id'], 'confirm')
    closed = decide(cards, D, card['id'], 'confirm')['card']
    assert closed['state'] == 'closed' and closed['open'] is False and closed['next_actor'] is None
    assert closed['next_check_at'] is None
    assert [g['state'] for g in closed['gates']] == ['done', 'done', 'done', 'done']
    assert [d['action'] for d in closed['decisions']] == ['confirm', 'deliver', 'acknowledge']
    assert {d['by'] for d in closed['decisions']} == {'3', '17'}


def test_approval_executed_needs_an_effect_receipt(cards):
    card = offer(cards, kind='approval', next_actor='17', title='Send the invite')
    assert card['state'] == 'opened'
    decided = decide(cards, V, card['id'], 'decide')['card']
    assert decided['state'] == 'decided' and decided['next_actor'] == '3'
    with pytest.raises(CRMError):
        decide(cards, D, card['id'], 'execute')
    done = decide(cards, D, card['id'], 'execute', receipt='receipt:mail/abc123')['card']
    assert done['state'] == 'closed' and [g['name'] for g in done['gates'] if g['state'] == 'done'] == [
        'decided', 'executed', 'closed']


def test_question_answer_then_used(cards):
    card = offer(cards, kind='question', title='Which venue?')
    answered = decide(cards, V, card['id'], 'answer', answer='Unicorn Factory, 8 Nov')['card']
    assert answered['state'] == 'answered' and answered['next_actor'] == '3'
    assert decide(cards, D, card['id'], 'use')['card']['state'] == 'closed'


def test_decline_is_the_next_actors_and_finishes_the_card(cards):
    card = offer(cards)
    out = decide(cards, V, card['id'], 'decline', reason='not this quarter')['card']
    assert out['state'] == 'declined' and out['open'] is False and out['viewer']['actions'] == []


# ── D13: the decision record ───────────────────────────────────────────────────────────────────
def test_a_decision_is_an_append_only_revision_with_class_nonce_and_content_hash(cards, store):
    card = offer(cards)
    n = cards.nonce(ORG, V, card['id'])
    cards.decide(ORG, V, card['id'], 'acknowledge', n['nonce'], n['content_hash'])
    with store.engine.connect() as c:
        rev = c.execute(select(s.revisions).where(s.revisions.c.record_id == card['id'])
                        .order_by(s.revisions.c.revision.desc())).mappings().first()
    assert rev['decision'] == 'acknowledge' and rev['credential_class'] == 'session'
    assert rev['nonce'] == n['nonce'] and rev['content_hash'] == n['content_hash'] and rev['actor_id'] == '17'
    with store.engine.connect() as c:
        first = c.execute(select(s.revisions).where(s.revisions.c.record_id == card['id'],
                                                    s.revisions.c.revision == 1)).mappings().first()
    assert first['decision'] == '' and first['credential_class'] == ''          # an ordinary revision


def test_a_nonce_is_single_use_personal_revision_bound_and_face_bound(cards, clock):
    card = offer(cards)
    n = cards.nonce(ORG, V, card['id'])
    cards.decide(ORG, V, card['id'], 'acknowledge', n['nonce'], n['content_hash'])
    with pytest.raises(Conflict):                                   # replay
        cards.decide(ORG, V, card['id'], 'deliver', n['nonce'], n['content_hash'], evidence=['page:team/a.md'])
    stale = cards.nonce(ORG, V, card['id'])
    cards.update(ORG, D, card['id'], cards.read(ORG, D, card['id'])['revision'], fields={'Summary': 'changed'})
    with pytest.raises(Conflict):                                   # the card moved since the face was shown
        cards.decide(ORG, V, card['id'], 'deliver', stale['nonce'], stale['content_hash'], evidence=['page:team/a.md'])
    fresh = cards.nonce(ORG, V, card['id'])
    with pytest.raises(Conflict):                                   # a face that is not the one shown
        cards.decide(ORG, V, card['id'], 'deliver', fresh['nonce'], '0' * 64, evidence=['page:team/a.md'])
    others = cards.nonce(ORG, D, card['id'])
    with pytest.raises(Conflict):                                   # minted for somebody else
        cards.decide(ORG, V, card['id'], 'deliver', others['nonce'], others['content_hash'], evidence=['page:team/a.md'])
    late = cards.nonce(ORG, V, card['id'])
    clock.at += timedelta(minutes=16)
    with pytest.raises(Conflict):                                   # expired
        cards.decide(ORG, V, card['id'], 'deliver', late['nonce'], late['content_hash'], evidence=['page:team/a.md'])


@pytest.mark.parametrize('who', [
    dataclasses.replace(V, credential_class='key'),                  # any API key, browser-scoped included
    dataclasses.replace(V, credential_class=''),                     # unknown is never session
    Caller(subject='17', workspaces=('team',), writable=('team',), delegated=True, regime='human',
           session='pchat-17', credential_class='delegation'),
])
def test_only_the_session_credential_decides_or_writes_seen(cards, who):
    card = offer(cards)
    for call in (lambda: cards.nonce(ORG, who, card['id']), lambda: cards.seen(ORG, who, card['id']),
                 lambda: cards.decide(ORG, who, card['id'], 'acknowledge', 'n' * 20, '0' * 64)):
        with pytest.raises(Refused):
            call()


def test_a_locked_field_blocks_the_viewers_decision(cards, store):
    """Deny test 17 on the decide path: you sign only what you can read."""
    with store.engine.begin() as c:
        c.execute(s.policies.insert().values(org_id=ORG, subject_id='17', policy={'active': True, 'objects': {
            'Card': {'read': 'none', 'read_fields': ['Title', 'State', 'Face'], 'write': 'none'}}}))
    card = cards.propose(ORG, D_AGENT, title='Renewal', workspace='team', reason='r',
                         fields={'Kind': 'approval', 'NextActor': '17', 'Summary': 'margin is 12%'},
                         face=[{'type': 'text', 'field': 'Summary', 'label': 'Summary'}])['card']
    assert {'type': 'locked', 'label': 'Summary'} in cards.read(ORG, V, card['id'])['face']
    with pytest.raises(Forbidden):
        decide(cards, V, card['id'], 'decide')


# ── D14: seen is the terminal's, on render ─────────────────────────────────────────────────────
def test_seen_is_written_only_by_the_render_and_shown_to_the_sender_only_by_policy(store, clock):
    quiet = Cards(store, clock=clock)
    card = offer(quiet)
    quiet.read(ORG, V, card['id'])
    assert quiet.read(ORG, V, card['id'])['viewer']['seen'] is False            # a read is not a render
    quiet.seen(ORG, V, card['id'])
    assert quiet.read(ORG, V, card['id'])['viewer']['seen'] is True
    assert quiet.read(ORG, V, card['id'])['handoff']['seen'] is True              # theirs to know
    assert 'seen' not in quiet.read(ORG, D, card['id'])['handoff']                # N-7: off by default
    told = Cards(store, clock=clock, policy_text=lambda: '---\ncard_seen_visible_to_sender: true\n---\n')
    assert told.read(ORG, D, card['id'])['handoff']['seen'] is True


def test_seen_before_a_new_offer_does_not_count_for_it(cards, clock):
    card = offer(cards, kind='question')
    cards.seen(ORG, V, card['id'])
    clock.at += timedelta(minutes=5)
    decide(cards, V, card['id'], 'answer', answer='yes')
    assert cards.read(ORG, D, card['id'])['viewer']['seen'] is False             # D has not seen the answer yet


# ── the inbox: one predicate for the Now strip, the Inbox and the room ──────────────────────────
def test_counts_match_across_the_inbox_and_the_room(cards):
    a = offer(cards, title='Contract')
    offer(cards, title='Catering', kind='approval')
    offer(cards, title='For D', next_actor='3', requester='17')
    inbox = cards.inbox(ORG, V)
    room = cards.list(ORG, V, workspace='team')
    assert inbox['counts']['waiting_on_me'] == room['counts']['waiting_on_me'] == len(inbox['items']) == 2
    assert {c['title'] for c in inbox['items']} == {'Contract', 'Catering'}
    assert cards.inbox(ORG, D)['counts'] == {'waiting_on_me': 1, 'due_soon': 3, 'i_wait_on': 2}
    decide(cards, V, a['id'], 'acknowledge')
    assert cards.inbox(ORG, V)['counts']['waiting_on_me'] == 2                    # still V's move: deliver
    decide(cards, V, a['id'], 'deliver', evidence=['meeting:153'])
    assert cards.inbox(ORG, V)['counts']['waiting_on_me'] == cards.list(ORG, V, workspace='team')['counts']['waiting_on_me'] == 1
    assert cards.inbox(ORG, D)['counts']['waiting_on_me'] == 2
    with pytest.raises(Refused):
        cards.inbox(ORG, D_AGENT)                                                # a person's own view


# ── D8: the producer computes the audience; card facts carry lifecycle fields only ───────────────
def test_card_facts_carry_audience_and_lifecycle_and_no_content(cards, store):
    card = offer(cards, Summary='margin is 12%')
    decide(cards, V, card['id'], 'acknowledge')
    events = _outbox(store, card['id'])
    env = events[-1]['envelope']
    assert env['v'] == 2 and env['producer'] == 'crm' and env['object'] == 'card'
    assert set(env['audience']) == {'3', '17', 'ws:team'} and env['owner'] == '3'
    assert env['lifecycle'] == {'kind': 'commitment', 'state': 'accepted', 'next_actor': '17',
                                'next_check_at': env['lifecycle']['next_check_at'], 'handoff': 'acknowledged'}
    blob = json.dumps(events)
    assert 'Send the venue contract' not in blob and 'margin' not in blob        # C8 / M5: no content
    assert events[-1]['subject_refs']['uid'] == '17'
    assert '40' not in env['audience']


def test_a_plain_record_fact_reaches_its_owner(store):
    rid = store.create(ORG, D, 'Company', {'Name': 'Globex'}, reason='r', idempotency_key='g')['record_id']
    env = _outbox(store, rid)[-1]['envelope']
    assert env == {'v': 2, 'producer': 'crm', 'object': 'record', 'owner': '3', 'visibility': 'owner', 'audience': ['3']}


# ── an agent never moves it; a room's agent can offer it ────────────────────────────────────────
def test_a_room_agent_offers_with_the_speaker_as_requester_and_never_decides(cards):
    out = cards.propose(ORG, ROOM_AGENT, title='Book the room', workspace='team', reason='said on the call',
                        fields={'Kind': 'commitment', 'NextActor': '17'})
    assert out['status'] == 'offered'
    rid = (out['card'] or {}).get('id') or out['record_id']
    assert cards.read(ORG, V, rid)['requester'] == '3'
    with pytest.raises(Refused):
        cards.decide(ORG, ROOM_AGENT, rid, 'acknowledge', 'n' * 20, '0' * 64)


def test_offers_are_capped_per_session_per_day(cards):
    for i in range(20):
        offer(cards, title=f'Card {i}')
    with pytest.raises(Refused):
        offer(cards, title='one too many')


# ── H9: the audit row is written in the operation's own transaction ─────────────────────────────
def test_a_decision_and_its_audit_row_commit_together(store, clock):
    cards = Cards(store, clock=clock)
    card = offer(cards)
    answers = {'v-session': {'user_id': '17', 'workspaces': ['team'], 'writable_workspaces': ['team'],
                             'credential_class': 'session'}}

    def resolve(request):
        token = json.loads(request.content)['token']
        return httpx.Response(200, json=answers[token]) if token in answers else httpx.Response(401)
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(resolve)), ORG))
    hdr = {'Authorization': 'Bearer v-session', 'x-trace-id': 't-1', 'x-vexa-taint': 'none'}
    n = client.post('/card/nonce', json={'card': card['id']}, headers=hdr).json()
    r = client.post('/card/decide', json={'card': card['id'], 'action': 'acknowledge', 'nonce': n['nonce'],
                                          'content_hash': n['content_hash']}, headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()['decision']['credential_class'] == 'session'
    with store.engine.connect() as c:
        rows = c.execute(select(s.audit).where(s.audit.c.op == 'card.decide.acknowledge')).mappings().all()
    assert len(rows) == 1 and rows[0]['record_id'] == card['id'] and rows[0]['caller'] == 'person'
    seen = client.post('/card/seen', json={'card': card['id']}, headers=hdr)
    assert seen.status_code == 200 and seen.json()['card'] == card['id']
    inbox = client.post('/card/inbox', json={}, headers=hdr).json()
    assert inbox['counts']['waiting_on_me'] == 1


def test_a_write_that_cannot_audit_does_not_commit(store, clock, monkeypatch):
    """H9 same transaction: if the audit insert fails, the decision is rolled back with it."""
    from crm import store as store_mod
    cards = Cards(store, clock=clock)
    card = offer(cards)
    n = cards.nonce(ORG, V, card['id'])
    token = store_mod.PENDING_AUDIT.set({'org': ORG, 'caller': V, 'op': 'card.decide', 'record_id': card['id']})
    real = s.audit.insert

    def broken():
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr(s.audit, 'insert', broken)
    try:
        with pytest.raises(RuntimeError):
            cards.decide(ORG, V, card['id'], 'acknowledge', n['nonce'], n['content_hash'])
    finally:
        store_mod.PENDING_AUDIT.reset(token)
        monkeypatch.setattr(s.audit, 'insert', real)
    assert cards.read(ORG, V, card['id'])['state'] == 'proposed'                 # nothing committed


# ── schema 7: an existing org's Card type gains the lifecycle properties additively ─────────────
def test_upgrade_adds_the_lifecycle_properties_and_keeps_an_administrators_own():
    engine = _engine()
    old = json.loads(json.dumps(DEFINITIONS['Card']))
    for k in CARD_LIFECYCLE_PROPERTIES:
        old['properties'].pop(k)
    old['properties']['Origin'] = {'type': 'string', 'maxLength': 50, 'description': 'admin-defined'}
    with engine.begin() as c:
        c.execute(s.orgs.insert().values(org_id='old', name='Old', default_policy={'active': True, 'objects': {}},
                                         created_at='2026-01-01'))
        c.execute(s.object_types.insert().values(org_id='old', name='Card', definition=old))
    assert upgrade_card_definition(engine, 'old') is True
    with engine.connect() as c:
        got = c.execute(select(s.object_types.c.definition).where(s.object_types.c.org_id == 'old')).scalar_one()
    assert set(CARD_LIFECYCLE_PROPERTIES) <= set(got['properties'])
    assert got['properties']['Origin']['description'] == 'admin-defined'
    assert upgrade_card_definition(engine, 'old') is False                       # idempotent


# ── crm.v1: the new shapes conform (goldens: CRM_WRITE_GOLDENS=1) ───────────────────────────────
def test_the_s5_shapes_conform_to_crm_v1(cards):
    from test_cards import conforms
    card = offer(cards)
    n = cards.nonce(ORG, V, card['id'])
    conforms('CardNonce', {**n, 'nonce': 'n' * 32, 'content_hash': 'a' * 64}, 'issued')
    out = cards.decide(ORG, V, card['id'], 'acknowledge', n['nonce'], n['content_hash'])
    conforms('CardDecided', out)
    conforms('CardView', out['card'], 'offered-and-acknowledged')
    conforms('CardInbox', cards.inbox(ORG, V))


def test_every_card_fact_seals_including_a_finished_one(cards, store):
    """event.v2's sealer refuses empty strings: a closed card's fact (no next actor) still seals."""
    from crm import fact_envelope
    card = offer(cards, kind='question')
    decide(cards, V, card['id'], 'answer', answer='yes')
    decide(cards, D, card['id'], 'use')
    for ev in _outbox(store, card['id']):
        sealed = fact_envelope.seal(ev['envelope'], 'test-secret')
        assert fact_envelope.verify(sealed, 'test-secret', ('crm',)) is not None
    assert 'next_actor' not in _outbox(store, card['id'])[-1]['envelope']['lifecycle']


# ── E2: the post-meeting run offers a meeting's follow-ups as the room's workspace principal ─────
def test_the_meeting_offer_door_makes_offered_cards_once_as_the_room_principal(store, clock):
    store.apply_workspace_snapshot(ORG, 'team', 'c1', '2026-10-17T10:00:00Z', [],
                                   [{'subject': '3', 'role': 'owner'}, {'subject': '17', 'role': 'contributor'}])
    client = TestClient(create_app(store, Identity('http://identity', 's', httpx.MockTransport(
        lambda r: httpx.Response(401))), ORG, internal_secret='internal-s'))
    body = {'principal': '900', 'workspace': 'team', 'on_behalf_of': '3', 'session': 'meet-153',
            'origin': 'meeting:153', 'reason': 'Said in the venue call', 'cards': [
                {'title': 'Send the venue contract by Friday', 'kind': 'commitment', 'next_actor': '17',
                 'due': '2026-10-23', 'about': 'meeting:153'},
                {'title': 'Send the sponsor deck tomorrow', 'kind': 'commitment', 'next_actor': '3', 'due': '2026-10-18'},
                {'title': 'Do we stream the meetup?', 'kind': 'question', 'next_actor': '3'}]}
    assert client.post('/internal/cards/offer', json=body).status_code == 403          # internal tier only
    hdr = {'X-Internal-Secret': 'internal-s'}
    r = client.post('/internal/cards/offer', json=body, headers=hdr)
    assert r.status_code == 200, r.text
    made = r.json()['cards']
    assert [c['kind'] for c in made] == ['commitment', 'commitment', 'question'] and not any(c['existing'] for c in made)
    again = client.post('/internal/cards/offer', json=body, headers=hdr).json()['cards']
    assert [c['id'] for c in again] == [c['id'] for c in made] and all(c['existing'] for c in again)   # idempotent
    cards = Cards(store, clock=clock)
    v = cards.read(ORG, V, made[0]['id'])
    assert v['origin'] == 'meeting:153' and v['about'] == {'kind': 'meeting', 'ref': 'meeting:153'}
    assert v['requester'] == '3' and v['next_actor'] == '17' and v['due'] == '2026-10-23'
    assert v['handoff']['phase'] == 'offered' and v['workspaces'] == ['team']
    assert cards.inbox(ORG, V)['counts']['waiting_on_me'] == 1
    assert cards.inbox(ORG, D)['counts'] == {'waiting_on_me': 2, 'due_soon': 1, 'i_wait_on': 1}
    with store.engine.connect() as c:
        owner = c.execute(select(s.records.c.owner_id).where(s.records.c.id == made[0]['id'])).scalar_one()
    assert owner == '900'                                                               # the room, not a person
    decide(cards, V, made[0]['id'], 'acknowledge')                                      # Valeriia accepts hers
    assert cards.read(ORG, D, made[0]['id'])['last_decision']['action'] == 'acknowledge'
