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
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==1
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
    monkeypatch.setattr(common, 'require_internal_secret', lambda: 'internal')
    monkeypatch.setattr(common, 'http', lambda m, url, h, body, timeout=20: sent.append((url, h, body)) or
                        (200, {'status': 'applied', 'references': 1}))
    out = step(SimpleNamespace(refs=SNAP))
    assert isinstance(out, Done) and out.result['references'] == 1
    url, headers, body = sent[0]
    assert url == 'http://crm-api:8300/internal/references' and headers == {'X-Internal-Secret': 'internal'}
    assert 'uid' not in body and body['members'] == SNAP['members']
    monkeypatch.setattr(common, 'http', lambda *a, **k: (503, {}))
    with pytest.raises(StepError):
        step(SimpleNamespace(refs=SNAP))
