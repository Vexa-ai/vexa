import httpx
from sqlalchemy import create_engine, select
from crm import schema as s
from crm.outbox import deliver

def test_failed_delivery_retries_same_id_without_record_values():
    engine=create_engine('sqlite://');s.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(s.outbox.insert().values(tenant_id='t',id='event',record_id='r',revision=1,delivered=False,
            event={'event_type':'crm.record.changed','source_event_id':'event','subject_refs':{'tenant_id':'t','record_id':'r','revision':1}}))
    calls=[]; status=[503,202]
    def receive(request):
        calls.append(request)
        return httpx.Response(status.pop(0))
    transport=httpx.MockTransport(receive)
    assert deliver(engine,'http://flows','secret',transport=transport)['pending']
    assert deliver(engine,'http://flows','secret',transport=transport)['delivered']==1
    assert calls[0].content == calls[1].content
    assert b'fields' not in calls[0].content
    assert calls[0].headers['X-Flows-Admin-Key']=='secret'
    assert deliver(engine,'http://flows','secret',transport=transport)['delivered']==0
    with engine.connect() as c:assert c.scalar(select(s.outbox.c.delivered))

def test_optional_flows_does_not_require_database_connection():
    assert deliver(None,'','')['reason']=='Flows is not configured'
