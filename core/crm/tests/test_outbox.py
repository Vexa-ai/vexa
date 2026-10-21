import pytest
import httpx
from sqlalchemy import create_engine, select
from crm import schema as s
from crm.outbox import deliver

def test_failed_delivery_retries_same_id_without_record_values():
    engine=create_engine('sqlite://');s.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(s.outbox.insert().values(org_id='t',id='event',record_id='r',revision=1,delivered=False,
            event={'event_type':'crm.record.changed','source_event_id':'event','subject_refs':{'org_id':'t','record_id':'r','revision':1}}))
    calls=[]; status=[503,202]
    def receive(request):
        calls.append(request)
        return httpx.Response(status.pop(0))
    transport=httpx.MockTransport(receive)
    assert deliver(engine,'http://flows','secret',transport=transport)['pending']
    assert deliver(engine,'http://flows','secret',transport=transport)['delivered']==1
    assert calls[0].content == calls[1].content
    assert b'fields' not in calls[0].content
    # the CRM's publish key, never flows' operator key (security review 19.1)
    assert calls[0].headers['X-Flows-Publisher-Key']=='secret'
    assert 'X-Flows-Operator-Key' not in calls[0].headers and 'X-Flows-Admin-Key' not in calls[0].headers
    assert deliver(engine,'http://flows','secret',transport=transport)['delivered']==0
    with engine.connect() as c:assert c.scalar(select(s.outbox.c.delivered))

def test_optional_flows_does_not_require_database_connection():
    assert deliver(None,'','')['reason']=='Flows is not configured'


# ── event.v2 (PRD 0.13.4 D8/C8): the outbox seals the card fact's envelope ───────────────────────
import json
from crm import fact_envelope

ENVELOPE = {'v': 2, 'producer': 'crm', 'object': 'card', 'owner': '11', 'visibility': 'members',
            'audience': ['11', '17', 'ws:sales'],
            'lifecycle': {'kind': 'commitment', 'state': 'proposed', 'next_actor': '17',
                          'next_check_at': '2026-10-18T09:00:00+00:00', 'handoff': 'offered'}}
REFS = {'org_id': 't', 'record_id': 'r', 'revision': 3, 'uid': '11'}


def _outbox(*events):
    engine = create_engine('sqlite://'); s.metadata.create_all(engine)
    with engine.begin() as c:
        for i, event in enumerate(events):
            c.execute(s.outbox.insert().values(org_id='t', id=f'event-{i}', record_id='r', revision=3 + i,
                                               delivered=False, event=event))
    return engine


def _capture():
    calls = []
    return calls, httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(202))


def test_a_card_fact_is_posted_with_its_envelope_sealed_by_the_crm_key():
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e1', 'subject_refs': REFS,
                      'envelope': ENVELOPE})
    calls, transport = _capture()
    assert deliver(engine, 'http://flows', 'k', transport=transport, seal_secret='seal')['delivered'] == 1
    body = json.loads(calls[0].content)
    assert set(body) == {'event_type', 'source_event_id', 'refs', 'envelope'}
    assert body['refs'] == REFS
    the_fact = fact_envelope.fact('crm.record.changed', 'e1', REFS)
    verified = fact_envelope.verify(body['envelope'], 'seal', {'crm'}, the_fact)
    assert {k: v for k, v in verified.items() if k != 'exp'} == ENVELOPE
    assert fact_envelope.verify(body['envelope'], 'other', {'crm'}, the_fact) is None


def test_deny_the_posted_seal_covers_the_posted_fact_and_expires():
    """The envelope the outbox posts verifies only over the event type, event id and refs it is posted
    with, and only until its expiry: it cannot be lifted onto another subject or record, or replayed."""
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e1', 'subject_refs': REFS,
                      'envelope': ENVELOPE})
    calls, transport = _capture()
    deliver(engine, 'http://flows', 'k', transport=transport, seal_secret='seal')
    body = json.loads(calls[0].content)
    env = body['envelope']
    for other in ({**REFS, 'uid': '17'}, {**REFS, 'record_id': 'r2'}, {**REFS, 'revision': 4}):
        assert fact_envelope.verify(env, 'seal', {'crm'},
                                    fact_envelope.fact('crm.record.changed', 'e1', other)) is None
    assert fact_envelope.verify(env, 'seal', {'crm'}, fact_envelope.fact('crm.record.changed', 'e2', REFS)) is None
    assert fact_envelope.verify(env, 'seal', {'crm'}, fact_envelope.fact('crm.other', 'e1', REFS)) is None
    assert fact_envelope.verify(env, 'seal', {'crm'}, fact_envelope.fact('crm.record.changed', 'e1', REFS),
                                now=env['exp']) is None


def test_an_old_row_without_an_envelope_is_posted_unchanged():
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e0', 'subject_refs': REFS})
    calls, transport = _capture()
    assert deliver(engine, 'http://flows', 'k', transport=transport, seal_secret='seal')['delivered'] == 1
    assert json.loads(calls[0].content) == {'event_type': 'crm.record.changed', 'source_event_id': 'e0',
                                            'refs': REFS}


def test_no_title_or_summary_is_ever_in_the_posted_body():
    fat = dict(ENVELOPE, title='Send the contract', summary='private words')
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e2', 'subject_refs': REFS,
                      'envelope': fat},
                     {'event_type': 'crm.record.changed', 'source_event_id': 'e3', 'subject_refs': REFS,
                      'envelope': dict(ENVELOPE, lifecycle=dict(ENVELOPE['lifecycle'], title='x'))})
    calls, transport = _capture()
    out = deliver(engine, 'http://flows', 'k', transport=transport, seal_secret='seal')
    assert out == {'delivered': 0, 'pending': True, 'reason': 'Envelope could not be sealed'}
    assert calls == []          # refused at the producer, never posted unsealed or fat
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e4', 'subject_refs': REFS,
                      'envelope': ENVELOPE})
    calls, transport = _capture()
    deliver(engine, 'http://flows', 'k', transport=transport, seal_secret='seal')
    assert b'title' not in calls[0].content and b'summary' not in calls[0].content


def test_deny_the_seal_secret_never_falls_back_to_the_internal_secret(monkeypatch):
    monkeypatch.delenv('VEXA_FACT_SEAL_SECRET', raising=False)
    monkeypatch.setenv('INTERNAL_API_SECRET', 'internal')
    assert fact_envelope.secret_from_env() == ''
    monkeypatch.setenv('VEXA_FACT_SEAL_SECRET', 'own')
    assert fact_envelope.secret_from_env() == 'own'


def test_deny_with_no_seal_secret_a_card_fact_stays_pending_and_is_never_posted(monkeypatch):
    monkeypatch.delenv('VEXA_FACT_SEAL_SECRET', raising=False)
    monkeypatch.setenv('INTERNAL_API_SECRET', 'internal')
    engine = _outbox({'event_type': 'crm.record.changed', 'source_event_id': 'e1', 'subject_refs': REFS,
                      'envelope': ENVELOPE})
    calls, transport = _capture()
    out = deliver(engine, 'http://flows', 'k', transport=transport)
    assert out == {'delivered': 0, 'pending': True, 'reason': 'Envelope could not be sealed'}
    assert calls == []


def test_deny_the_delivery_process_refuses_to_start_without_its_seal_secret(monkeypatch):
    import runpy
    import sys
    monkeypatch.delenv('VEXA_FACT_SEAL_SECRET', raising=False)
    monkeypatch.setenv('INTERNAL_API_SECRET', 'internal')
    monkeypatch.setenv('CRM_DATABASE_URL', 'sqlite://')
    monkeypatch.setattr(sys, 'argv', ['crm.outbox'])
    sys.modules.pop('crm.outbox', None)
    with pytest.raises(SystemExit) as stop:
        runpy.run_module('crm.outbox', run_name='__main__')
    assert 'VEXA_FACT_SEAL_SECRET' in str(stop.value.code)


# ── the outbox's own boot check (security review 19.2) ──────────────────────────────────────────
OUTBOX_ENV = {'CRM_DATABASE_URL': 'sqlite://', 'VEXA_FACT_SEAL_SECRET': 's' * 64,
              'FLOWS_API_URL': 'http://flows-api:8200', 'FLOWS_PUBLISH_KEY': 'p' * 64}


def test_the_outbox_boots_on_its_own_keys_without_crm_apis():
    from crm.outbox import preflight
    preflight(dict(OUTBOX_ENV))
    preflight({k: v for k, v in OUTBOX_ENV.items() if k not in ('FLOWS_API_URL', 'FLOWS_PUBLISH_KEY')})


@pytest.mark.parametrize('change', [
    {'FLOWS_PUBLISH_KEY': ''}, {'FLOWS_PUBLISH_KEY': 'changeme'}, {'VEXA_FACT_SEAL_SECRET': ''},
    {'CRM_DATABASE_URL': ''}])
def test_deny_the_outbox_refuses_to_start_on_a_missing_or_placeholder_key(change):
    from crm.outbox import preflight
    with pytest.raises(SystemExit) as stop:
        preflight({**OUTBOX_ENV, **change})
    key = next(iter(change))
    assert key in str(stop.value.code) and 'changeme' not in str(stop.value.code)
