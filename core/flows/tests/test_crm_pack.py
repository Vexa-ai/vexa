from types import SimpleNamespace
import pytest
from fixtures import rig
from flows import admit, StepError
from flows_defs.crm import build


def test_record_event_admission_is_idempotent_and_receipt_contains_only_references():
    db,reg,clock,_=rig()
    assert not reg.match('crm.record.changed')
    build(reg,db)
    refs={'tenant_id':'t','record_id':'r','revision':2,'uid':'7'}
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==1
    assert admit(db,reg,clock,source_event_id='crm-event',event_type='crm.record.changed',subject_refs=refs)==0
    receipt=reg.steps['record_crm_change'](SimpleNamespace(refs={**refs,'fields':{'secret':'not copied'}}))
    assert receipt.result=={'tenant_id':'t','record_id':'r','revision':2}
    with pytest.raises(StepError):reg.steps['record_crm_change'](SimpleNamespace(refs={}))
