"""Bounded, retryable delivery to Flows. No CRM field values leave the record service, except the
enumerated lifecycle coordination fields of a card fact (kind, state, next_actor, next_check_at,
handoff — PRD 0.13.4 D8/C8).

A row whose event carries an `envelope` (event.v2) is SEALED here, at delivery, with the CRM's own
Ed25519 private key, bound to the fact it rides on (event type, event id and refs) and good for
`fact_envelope.SEAL_TTL_S`; it is posted as `{event_type, source_event_id, refs, envelope}`. flows
admits it to the subject's flows and to every person in the envelope's audience, and refuses it 403
if the seal does not verify under the CRM's public key (`crm.pem` in flows-api's verify directory).
The key is the PEM file at `VEXA_FACT_SEAL_KEY_FILE`, which only this process holds, and the
delivery process refuses to start without it. A refused row stays pending and is sealed afresh on
the next pass, so a row refused while the two sides' keys disagree (an upgrade in progress) is
delivered once they agree. It posts with the CRM's publish key
(`FLOWS_PUBLISH_KEY`, `X-Flows-Publisher-Key`), which admits the CRM's own facts and nothing else. A row without an `envelope` is
posted unsealed and reaches its subject only. Neither ever carries a title, a summary or any other field value: refs are
references (M5) and the envelope's keys are enumerated by `fact_envelope`."""
import os
import httpx
from sqlalchemy import create_engine, select, update
from . import fact_envelope
from . import fact_keys
from . import schema as s

#: The CRM's own Ed25519 private key file (config.v1 `VEXA_FACT_SEAL_KEY_FILE`); only the outbox holds it.
SEAL_KEY_FILE_ENV = 'VEXA_FACT_SEAL_KEY_FILE'


def signer_from_env(env=None):
    """The CRM's signer from the file `VEXA_FACT_SEAL_KEY_FILE` names, else None."""
    env = os.environ if env is None else env
    return fact_keys.signer_from_file(env.get(SEAL_KEY_FILE_ENV))


def _body(event, sign):
    """The POST body for one outbox event. Raises ValueError when an envelope cannot be sealed."""
    body = {'event_type': event['event_type'], 'source_event_id': event['source_event_id'],
            'refs': event['subject_refs']}
    if event.get('envelope') is not None:
        body['envelope'] = fact_envelope.seal(event['envelope'], sign, fact_envelope.fact(
            body['event_type'], body['source_event_id'], body['refs']))
    return body


def deliver(engine, endpoint, token, *, limit=100, record_id=None, transport=None, sign=None):
    if not endpoint or not token:
        return {'delivered': 0, 'pending': True, 'reason': 'Flows is not configured'}
    delivered = 0
    sign = signer_from_env() if sign is None else sign
    with engine.begin() as connection:
        query = select(s.outbox).where(s.outbox.c.delivered == False).order_by(s.outbox.c.id).limit(limit)
        if record_id:
            query = query.where(s.outbox.c.record_id == record_id)
        # A crash after Flows accepts but before commit redelivers the SAME event ID.
        rows = connection.execute(query.with_for_update(skip_locked=True)).mappings().all()
        with httpx.Client(transport=transport, timeout=10) as client:
            for row in rows:
                try:
                    body = _body(row['event'], sign)
                except ValueError:
                    # A row that cannot be sealed stays pending and says so — never posted unsealed.
                    return {'delivered': delivered, 'pending': True, 'reason': 'Envelope could not be sealed'}
                try:
                    response = client.post(endpoint.rstrip('/') + '/events',
                        headers={'X-Flows-Publisher-Key': token}, json=body)
                except httpx.HTTPError:
                    return {'delivered': delivered, 'pending': True, 'reason': 'Flows unavailable'}
                if response.status_code != 202:
                    return {'delivered': delivered, 'pending': True, 'status': response.status_code}
                connection.execute(update(s.outbox).where(s.outbox.c.org_id == row['org_id'],
                    s.outbox.c.id == row['id']).values(delivered=True))
                delivered += 1
    return {'delivered': delivered, 'pending': len(rows) == limit}

def preflight(env=None):
    """The outbox's own boot check, against crm's config.v1 declaration: its login, the fact seal key
    and, where flows is named, the publish key — each set and none a published placeholder, and the
    seal key a readable Ed25519 private key. crm-api's keys (the org, identity's door, the internal
    secret) are not the outbox's, so crm-api's preflight is not this one. Raises SystemExit naming
    every key, never a value."""
    from .config_preflight import load_declaration
    env = os.environ if env is None else env
    need = ['CRM_DATABASE_URL', SEAL_KEY_FILE_ENV]
    if (env.get('FLOWS_API_URL') or '').strip():
        need.append('FLOWS_PUBLISH_KEY')
    missing = [k for k in need if not (env.get(k) or '').strip()]
    if missing:
        raise SystemExit(f"crm outbox refuses to start — not set: {', '.join(missing)}. The outbox seals "
                         f"every card fact it delivers with the CRM's own key ({SEAL_KEY_FILE_ENV}, "
                         "whose public half flows-api verifies under) and posts it with the CRM's publish key")
    forbidden = {e['key']: set(e.get('forbidden_values') or ()) for e in load_declaration().get('keys') or []}
    held = [k for k in need if (env.get(k) or '').strip() in forbidden.get(k, set())]
    if held:
        raise SystemExit(f"crm outbox refuses to start — {', '.join(held)} hold(s) a published "
                         "placeholder; mint a real value (`openssl rand -hex 32`)")
    if signer_from_env(env) is None:
        raise SystemExit(f"crm outbox refuses to start — {SEAL_KEY_FILE_ENV} is not a readable "
                         "Ed25519 private key in PKCS#8 PEM (`openssl genpkey -algorithm ed25519`)")


if __name__ == '__main__':
    import argparse
    import json
    import time
    parser=argparse.ArgumentParser()
    parser.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    preflight()
    engine=create_engine(os.environ['CRM_DATABASE_URL'],pool_pre_ping=True,pool_size=1,max_overflow=0)
    while True:
        try:
            result=deliver(engine,os.environ.get('FLOWS_API_URL',''),os.environ.get('FLOWS_PUBLISH_KEY',''))
        except Exception:
            # Driver exception strings can contain credentials or SQL values.
            result={'delivered':0,'pending':True,'reason':'Delivery failed; retry pending'}
        if result.get('delivered') or result.get('reason') or result.get('status') or not args.watch:
            print(json.dumps(result),flush=True)
        if not args.watch:break
        time.sleep(1 if result.get('delivered') else 10)
