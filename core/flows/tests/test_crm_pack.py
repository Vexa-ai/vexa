from types import SimpleNamespace
import pytest
from fixtures import rig
from flows import admit, StepError
from flows_defs.crm import build


def test_record_event_admission_is_idempotent_and_receipt_contains_only_references():
    db,reg,clock,_=rig()
    assert not reg.match('crm.record.changed')
    build(reg,db)
    refs={'org_id':'t','record_id':'r','revision':2,'uid':'7'}
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==2   # crm_change_log + card_dependents_wake
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==0
    receipt=reg.steps['record_crm_change'](SimpleNamespace(refs={**refs,'fields':{'secret':'not copied'}}))
    assert receipt.result=={'org_id':'t','record_id':'r','revision':2}
    with pytest.raises(StepError):reg.steps['record_crm_change'](SimpleNamespace(refs={}))


SNAP = {'workspace': 'sales', 'commit': 'c1', 'committed_at': '2026-10-13T10:00:00Z',
        'references': [{'page': 'a.md', 'record': 'r'}], 'members': [{'subject': '2', 'role': 'viewer'}], 'uid': '1'}


def test_references_reach_the_crm_only_where_one_is_deployed(monkeypatch):
    from flows import NotPresent, Done
    db, reg, clock, _ = rig()
    build(reg, db)
    assert [f.name for f in reg.match('workspace.references.changed')] == ['crm_references']
    step = reg.steps['hand_references_to_crm']
    monkeypatch.delenv('VEXA_FLOWS_CRM_API_URL', raising=False)
    assert isinstance(step(SimpleNamespace(refs=SNAP)), NotPresent)
    sent = []
    import flows_steps.common as common
    monkeypatch.setenv('VEXA_FLOWS_CRM_API_URL', 'http://crm-api:8300')
    monkeypatch.setenv('VEXA_FLOWS_SERVICE_KEY', 'flows-service-key-crm')
    monkeypatch.setattr(common, 'http', lambda m, url, h, body, timeout=20: sent.append((url, h, body)) or
                        (200, {'status': 'applied', 'references': 1}))
    out = step(SimpleNamespace(refs=SNAP))
    assert isinstance(out, Done) and out.result['references'] == 1
    url, headers, body = sent[0]
    assert url == 'http://crm-api:8300/internal/references' and headers == {'X-Vexa-Service-Key': 'flows-service-key-crm'}
    assert 'uid' not in body and body['members'] == SNAP['members']
    monkeypatch.setattr(common, 'http', lambda *a, **k: (503, {}))
    with pytest.raises(StepError):
        step(SimpleNamespace(refs=SNAP))


# ── a card's blockers: card_blockers_done sleeps, card_dependents_wake wakes it ────────────────
from flows import Done as _Done, EventType, NotPresent as _NotPresent, Wait, tick
from flows_defs.crm import BLOCKERS_RECHECK_S, card_of


class FakeCRM:
    """The CRM's `/internal/cards/blockers`, from a dict: card -> {blocker: State}. Done and Dropped are
    the default lifecycle's finished and side states."""
    def __init__(self, chains, titles=None, missing=()):
        self.chains, self.titles, self.missing, self.calls = chains, titles or {}, set(missing), []

    def __call__(self, method, url, headers, body, timeout=20):
        self.calls.append((method, url, headers, body))
        card = body['card']
        if card not in self.chains:
            return 404, {'detail': 'Card not found'}
        out = {'card': card, 'blocked_by': list(self.chains[card]), 'open': [], 'done': [], 'dropped': [], 'missing': []}
        for b, state in self.chains[card].items():
            if b in self.missing:
                out['missing'].append(b)
            elif state == 'Done':
                out['done'].append(b)
            elif state == 'Dropped':
                out['dropped'].append({'id': b, 'state': state, 'title': self.titles.get(b, 'A card')})
            else:
                out['open'].append({'id': b, 'state': state})
        return 200, out


def _waiting_rig(monkeypatch, crm):
    import flows_steps.common as common
    monkeypatch.setenv('VEXA_FLOWS_CRM_API_URL', 'http://crm-api:8300')
    monkeypatch.setenv('VEXA_FLOWS_SERVICE_KEY', 'flows-service-key-crm')
    monkeypatch.setattr(common, 'http', crm)
    db, reg, clock, _ = rig()
    build(reg, db)

    @reg.step
    def obligation_after_blockers(ctx):
        return _Done({'ran': True})
    reg.flow(name='card_obligation', version=1, on=EventType('test.card.obligation'),
             steps=[reg.steps['card_blockers_done'], reg.steps['obligation_after_blockers']])
    return db, reg, clock


def _run(db, reg, clock):
    """Run everything due NOW without moving the clock (a Wait stays asleep)."""
    for _ in range(50):
        if not tick(db, reg, clock):
            return
    raise AssertionError('livelock')


def _row(db, flow):
    (row,) = db.execute('SELECT reaction_id, step, status, next_run_at, scratch FROM reaction WHERE flow = :f',
                        {'f': flow})
    return row


def _changed(db, reg, clock, record, n):
    return admit(db, reg, clock, source_event_id=f'crm-{record}-{n}', event_type='crm.record.changed',
                 subject_refs={'org_id': 't', 'record_id': record, 'revision': n, 'uid': '7'})


def test_the_card_named_by_a_reaction():
    assert card_of({'card': 'c1'}) == 'c1'
    assert card_of({'ref': 'card:c2'}) == 'c2'
    assert card_of({'ref': 'email:thread-9'}) == '' and card_of({}) == ''


def test_a_card_sleeps_until_every_blocker_is_done_and_a_change_wakes_it(monkeypatch):
    import json
    crm = FakeCRM({'c1': {'b1': 'Doing', 'b2': 'Done'}, 'b1': {}, 'x': {}})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    assert sorted(f.name for f in reg.match('crm.record.changed')) == ['card_dependents_wake', 'crm_change_log']
    assert admit(db, reg, clock, source_event_id='o-1', event_type='test.card.obligation',
                 subject_refs={'ref': 'card:c1', 'uid': '7'}) == 1
    _run(db, reg, clock)
    rid, step, status, nra, scratch = _row(db, 'card_obligation')
    assert (step, status) == ('card_blockers_done', 'retrying')              # asleep, no attempt burnt
    assert nra == clock.now() + BLOCKERS_RECHECK_S
    assert json.loads(scratch) == {'card': 'c1', 'open_blockers': ['b1']}
    method, url, headers, body = crm.calls[-1]
    assert (method, url, body) == ('POST', 'http://crm-api:8300/internal/cards/blockers', {'card': 'c1'})
    assert headers == {'X-Vexa-Service-Key': 'flows-service-key-crm'}

    clock.advance(60)
    assert _changed(db, reg, clock, 'x', 1) == 2                              # an unrelated record changes
    _run(db, reg, clock)
    assert _row(db, 'card_obligation')[2:4] == ('retrying', nra)              # still asleep, not woken
    assert not db.execute("SELECT 1 FROM signal WHERE reaction_id = :r", {'r': rid})

    crm.chains['c1']['b1'] = 'Done'                                           # the blocker is finished
    assert _changed(db, reg, clock, 'b1', 2) == 2
    _run(db, reg, clock)
    assert _row(db, 'card_obligation')[1:3] == ('obligation_after_blockers', 'done')
    (kind, actor, reason), = db.execute("SELECT kind, actor, reason FROM signal WHERE reaction_id = :r", {'r': rid})
    assert kind == 'wake' and actor.startswith('card_dependents_wake:') and reason == 'card b1 changed (revision 2)'
    (res,), = db.execute("SELECT result FROM effect_receipt WHERE reaction_id = :r AND step = 'card_blockers_done'",
                         {'r': rid})
    assert json.loads(res) == {'card': 'c1', 'waited_on': ['b1', 'b2'], 'done': ['b1', 'b2'], 'dropped': [], 'missing': []}


def test_editing_the_waiting_cards_own_chain_wakes_it(monkeypatch):
    crm = FakeCRM({'c1': {'b1': 'To do'}})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    admit(db, reg, clock, source_event_id='o-1', event_type='test.card.obligation', subject_refs={'card': 'c1', 'uid': '7'})
    _run(db, reg, clock)
    crm.chains['c1'] = {}                                                     # the blocker is taken off the card
    _changed(db, reg, clock, 'c1', 5)
    _run(db, reg, clock)
    assert _row(db, 'card_obligation')[2] == 'done'


def test_the_recheck_moves_a_card_whose_change_was_never_delivered(monkeypatch):
    crm = FakeCRM({'c1': {'b1': 'Doing'}})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    admit(db, reg, clock, source_event_id='o-1', event_type='test.card.obligation', subject_refs={'card': 'c1', 'uid': '7'})
    _run(db, reg, clock)
    crm.chains['c1']['b1'] = 'Done'                                           # no crm.record.changed arrives
    clock.advance(BLOCKERS_RECHECK_S)
    _run(db, reg, clock)
    assert _row(db, 'card_obligation')[2] == 'done'


def test_a_dropped_or_missing_blocker_lets_the_card_go_on_with_a_note_for_its_owner(monkeypatch):
    crm = FakeCRM({'c1': {'b1': 'Dropped', 'b2': 'Done', 'gone': 'Doing'}},
                  titles={'b1': 'Ship the old flow'}, missing={'gone'})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    step = reg.steps['card_blockers_done']
    out = step(SimpleNamespace(refs={'card': 'c1'}, scratch={}, flow=None))
    assert isinstance(out, _Done)
    assert out.result['dropped'] == [{'id': 'b1', 'state': 'Dropped', 'title': 'Ship the old flow'}]
    assert out.result['missing'] == ['gone'] and out.result['done'] == ['b2']
    assert '«Ship the old flow» ended Dropped' in out.result['note'] and 'gone is not a card' in out.result['note']


def test_no_card_no_crm_and_no_such_card(monkeypatch):
    crm = FakeCRM({})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    step = reg.steps['card_blockers_done']
    out = step(SimpleNamespace(refs={'ref': 'email:thread-1'}, scratch={}, flow=None))
    assert isinstance(out, _Done) and out.result == {'card': None, 'waited_on': []} and crm.calls == []
    with pytest.raises(StepError) as no_card:
        step(SimpleNamespace(refs={'card': 'nope'}, scratch={}, flow=None))
    assert no_card.value.retryable is False
    import flows_steps.common as common
    monkeypatch.setattr(common, 'http', lambda *a, **k: (503, {}))
    with pytest.raises(StepError) as down:
        step(SimpleNamespace(refs={'card': 'c1'}, scratch={}, flow=None))
    assert down.value.retryable is True
    monkeypatch.delenv('VEXA_FLOWS_CRM_API_URL', raising=False)
    assert isinstance(step(SimpleNamespace(refs={'card': 'c1'}, scratch={}, flow=None)), _NotPresent)


def test_a_flow_sets_its_own_recheck(monkeypatch):
    crm = FakeCRM({'c1': {'b1': 'Doing'}})
    db, reg, clock = _waiting_rig(monkeypatch, crm)
    flow = SimpleNamespace(param=lambda key, default=None: 600 if key == 'blockers_recheck_s' else default)
    out = reg.steps['card_blockers_done'](SimpleNamespace(refs={'card': 'c1'}, scratch={}, flow=flow))
    assert isinstance(out, Wait) and out.seconds == 600
