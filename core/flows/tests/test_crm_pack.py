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
    # two flows react: the change log, and the software card's stage rule (test_card_stage.py)
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==2
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


def test_business_events_are_kept_once_with_their_evidence():
    db, reg, clock, _ = rig()
    build(reg, db)
    for kind in ('registered', 'paid', 'bot', 'support', 'stage'):
        assert [f.name for f in reg.match(f'business.{kind}')] == [f'business_{kind}']
    ev = {'reading': 'stripe/charges', 'connection': 'stripe', 'path': '/v1/charges', 'read_at': 1.0}
    refs = {'email': 'a@x.io', '_evidence': ev}
    assert admit(db, reg, clock, source_event_id='sensor:instance/s:ch_1', event_type='business.paid', subject_refs=refs) == 1
    assert admit(db, reg, clock, source_event_id='sensor:instance/s:ch_1', event_type='business.paid', subject_refs=refs) == 0
    out = reg.steps['record_business_event'](SimpleNamespace(refs=refs))
    assert out.result == {'reading': 'stripe/charges', 'read_at': 1.0}
    with pytest.raises(StepError):
        reg.steps['record_business_event'](SimpleNamespace(refs={'email': 'a@x.io'}))


READING_EV = {'connection': 'github', 'reading': 'github/vexa-pulls', 'path': '/repos/Vexa-ai/vexa/pulls',
              'query': '/repos/Vexa-ai/vexa/pulls?state=all', 'cursor': '2026-10-31T00:00:00Z', 'read_at': 1_793_500_000.0}
PR_URL = 'https://github.com/Vexa-ai/vexa/pull/1962'
READING_REFS = {'subject': PR_URL, 'url': PR_URL, 'title': 'Per-account ramp limit', 'state': 'closed',
                'merged_at': '2026-10-31T18:02:11Z', 'head_sha': 'abc1234', 'labels': [{'name': 'bots'}],
                '_evidence': READING_EV, '_sensor': 'instance/github-pulls'}


def _crm(monkeypatch, answers):
    import flows_steps.common as common
    sent = []
    monkeypatch.setenv('VEXA_FLOWS_CRM_API_URL', 'http://crm-api:8300')
    monkeypatch.setenv('VEXA_FLOWS_SERVICE_KEY', 'flows-service-key-crm')

    def http(method, url, headers, body, timeout=20):
        sent.append((url, headers, body))
        return answers(url, body)
    monkeypatch.setattr(common, 'http', http)
    return sent


def test_a_reading_is_written_onto_every_card_that_tracks_it(monkeypatch):
    from flows import Done
    db, reg, clock, _ = rig()
    build(reg, db)
    assert [f.name for f in reg.match('business.reading')] == ['crm_reading']
    assert admit(db, reg, clock, source_event_id='sensor:instance/g:x@1', event_type='business.reading',
                 subject_refs=READING_REFS) == 1

    def answers(url, body):
        if url.endswith('/internal/cards/by-source'):
            return 200, {'subject': 'github.com/vexa-ai/vexa#1962', 'cards': ['c1', 'gone', 'c2']}
        if body['record'] == 'gone':
            return 404, {'detail': 'Card not found'}
        return 200, {'status': 'applied', 'revision': 4}
    sent = _crm(monkeypatch, answers)
    out = reg.steps['write_reading_to_crm'](SimpleNamespace(refs=READING_REFS))
    assert isinstance(out, Done)
    assert out.result['cards'] == [{'card': 'c1', 'status': 'applied', 'revision': 4},
                                   {'card': 'c2', 'status': 'applied', 'revision': 4}]
    url, headers, body = sent[0]
    assert url == 'http://crm-api:8300/internal/cards/by-source' and body == {'link': PR_URL}
    assert headers == {'X-Vexa-Service-Key': 'flows-service-key-crm'}
    url, headers, body = sent[1]
    assert url == 'http://crm-api:8300/internal/records/reading'
    # the declared reading fields only: no subject, no engine refs, no evidence among the fields
    assert body == {'record': 'c1', 'source': 'github/vexa-pulls',
                    'fields': {'url': PR_URL, 'title': 'Per-account ramp limit', 'state': 'closed',
                               'merged_at': '2026-10-31T18:02:11Z', 'head_sha': 'abc1234',
                               'labels': [{'name': 'bots'}]},
                    'evidence': {'read_at': 1_793_500_000.0, 'query': '/repos/Vexa-ai/vexa/pulls?state=all',
                                 'cursor': '2026-10-31T00:00:00Z'}}


def test_a_reading_no_card_tracks_writes_nothing(monkeypatch):
    db, reg, clock, _ = rig()
    build(reg, db)
    sent = _crm(monkeypatch, lambda url, body: (200, {'subject': 's', 'cards': []}))
    out = reg.steps['write_reading_to_crm'](SimpleNamespace(refs=READING_REFS))
    assert out.result['cards'] == [] and len(sent) == 1


def test_a_reading_needs_its_subject_evidence_and_a_crm(monkeypatch):
    from flows import NotPresent
    db, reg, clock, _ = rig()
    build(reg, db)
    step = reg.steps['write_reading_to_crm']
    for refs in ({**READING_REFS, 'subject': ''}, {k: v for k, v in READING_REFS.items() if k != '_evidence'},
                 {**READING_REFS, '_evidence': {'reading': 'github/vexa-pulls'}}):
        with pytest.raises(StepError) as e:
            step(SimpleNamespace(refs=refs))
        assert e.value.retryable is False
    monkeypatch.delenv('VEXA_FLOWS_CRM_API_URL', raising=False)
    assert isinstance(step(SimpleNamespace(refs=READING_REFS)), NotPresent)
    _crm(monkeypatch, lambda url, body: (503, {}))
    with pytest.raises(StepError) as e:
        step(SimpleNamespace(refs=READING_REFS))
    assert e.value.retryable is True
    _crm(monkeypatch, lambda url, body: ((200, {'cards': ['c1']}) if url.endswith('by-source')
                                         else (422, {'detail': 'not declared'})))
    with pytest.raises(StepError) as e:
        step(SimpleNamespace(refs=READING_REFS))
    assert e.value.retryable is False


def test_a_business_event_with_a_subject_lands_on_the_matched_person(monkeypatch):
    """L2.7 (founder 1 Nov 2026): a PostHog signup names its email as the subject; the CRM answers the matched
    person, and the reading lands there through the same reading route a card reading uses."""
    db, reg, clock, _ = rig()
    build(reg, db)
    refs = {"subject": "ann@globex.test", "event": "signup", "email": "ann@globex.test",
            "_evidence": {"connection": "posthog", "reading": "posthog/signup", "path": "/api/projects/1/events",
                          "query": "/api/projects/1/events?event=signup", "read_at": 1_793_560_000.0}}

    def answers(url, body):
        if url.endswith("/internal/cards/by-source"):
            return 200, {"subject": "email:ann@globex.test", "cards": ["p1"]}
        return 200, {"status": "applied", "revision": 2}
    sent = _crm(monkeypatch, answers)
    out = reg.steps["record_business_event"](SimpleNamespace(refs=refs))
    assert out.result["records"] == [{"card": "p1", "status": "applied", "revision": 2}]
    assert sent[0][2] == {"link": "ann@globex.test"}
    assert sent[1][2]["record"] == "p1" and sent[1][2]["source"] == "posthog/signup"
    assert sent[1][2]["fields"] == {"event": "signup", "email": "ann@globex.test"}
    # no subject: kept as a business event only, nothing written to the CRM
    sent.clear()
    out = reg.steps["record_business_event"](SimpleNamespace(refs={k: v for k, v in refs.items() if k != "subject"}))
    assert "records" not in out.result and sent == []
