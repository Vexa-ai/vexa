"""Bounded, retryable delivery to Flows. No CRM field values leave the record service."""
import os
import httpx
from sqlalchemy import create_engine, select, update
from . import schema as s


def deliver(engine, endpoint, token, *, limit=100, record_id=None, transport=None):
    if not endpoint or not token:
        return {'delivered': 0, 'pending': True, 'reason': 'Flows is not configured'}
    delivered = 0
    with engine.begin() as connection:
        query = select(s.outbox).where(s.outbox.c.delivered == False).order_by(s.outbox.c.id).limit(limit)
        if record_id:
            query = query.where(s.outbox.c.record_id == record_id)
        # A crash after Flows accepts but before commit redelivers the SAME event ID.
        rows = connection.execute(query.with_for_update(skip_locked=True)).mappings().all()
        with httpx.Client(transport=transport, timeout=10) as client:
            for row in rows:
                event = row['event']
                try:
                    response = client.post(endpoint.rstrip('/') + '/events',
                        headers={'X-Flows-Admin-Key': token, 'X-Actor': 'crm-outbox'},
                        json={'event_type': event['event_type'], 'source_event_id': event['source_event_id'],
                              'refs': event['subject_refs']})
                except httpx.HTTPError:
                    return {'delivered': delivered, 'pending': True, 'reason': 'Flows unavailable'}
                if response.status_code != 202:
                    return {'delivered': delivered, 'pending': True, 'status': response.status_code}
                connection.execute(update(s.outbox).where(s.outbox.c.tenant_id == row['tenant_id'],
                    s.outbox.c.id == row['id']).values(delivered=True))
                delivered += 1
    return {'delivered': delivered, 'pending': len(rows) == limit}

if __name__ == '__main__':
    import argparse
    import json
    import time
    parser=argparse.ArgumentParser()
    parser.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    engine=create_engine(os.environ['CRM_DATABASE_URL'],pool_pre_ping=True,pool_size=1,max_overflow=0)
    while True:
        try:
            result=deliver(engine,os.environ.get('FLOWS_API_URL',''),os.environ.get('FLOWS_API_KEY',''))
        except Exception:
            # Driver exception strings can contain credentials or SQL values.
            result={'delivered':0,'pending':True,'reason':'Delivery failed; retry pending'}
        if result.get('delivered') or result.get('reason') or result.get('status') or not args.watch:
            print(json.dumps(result),flush=True)
        if not args.watch:break
        time.sleep(1 if result.get('delivered') else 10)
