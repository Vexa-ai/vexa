"""Optional CRM pack, enabled through VEXA_FLOWS_DEFS_EXTRA=flows_defs.crm (ADR-0047).

Two flows, both carrying references and never account fields:

* ``crm_change_log`` records a CRM revision reference (``crm.record.changed``) in the timeline.
* ``crm_references`` hands a workspace's links to CRM records at one commit
  (``workspace.references.changed``, published by agent-api) to the CRM, which replaces that
  workspace's set. The fact is a full snapshot, so a retry or a redelivery changes nothing.

A fact grants no record access. With no CRM deployed (``VEXA_FLOWS_CRM_API_URL`` unset) the second
flow ends ``not_present`` rather than knocking on a missing door.

* ``business_<kind>`` (one per kind in ``BUSINESS_EVENTS``) keeps a business event a sensor read
  (``business.registered|paid|bot|support|stage|traffic``, from the generic ``reading`` sensor kind) as
  one reaction: the event's durable row, with its ``_evidence`` (connection, reading, path,
  read_at).
* ``crm_reading`` (``business.reading``) writes a reading onto the CRM cards it is about: the
  event's ``subject`` is a link (a GitHub issue or pull request URL, from the ``github`` connection's
  readings); the CRM answers which cards track it (``/internal/cards/by-source``: their typed
  ``Source``, or a legacy «GitHub: owner/repo#N» in ``Summary``), and the step writes the reading's
  declared fields with its evidence onto each (``/internal/records/reading``) as the card's
  ``Reading`` — never a field a person or an agent writes. The CRM keeps it idempotent by (record,
  source, read_at), so a retry or a redelivery changes nothing.
* ``crm_card_stage`` (``crm.record.changed``) and ``crm_card_stage_recheck`` (a schedule) keep a
  SOFTWARE card's stage true to the facts (founder, 1 Nov 2026). The stage is computed by the pure
  ``card_stage.stage(readings, flows_claim)`` from the card's readings and its live claim (the CRM's hold), and
  written through ``/internal/cards/stage`` (the only path that may set it) only when it differs,
  with the fact that moved it. On a record change the rule reads the card's STORED readings; the
  re-check, every ``RECHECK_CRON``, reads each open software card afresh (GET only, the connections
  file's ``delivery`` block: the pull request, its check runs, the prod pin, the newest releases),
  writes that as the card's ``github/delivery`` reading, and any move it makes is a CORRECTION the
  card shows («was X, now Y, because …»).
"""
import time
import flows_config
from flows import Done, EventType, NotPresent, StepError

CRM_URL_ENV = "VEXA_FLOWS_CRM_API_URL"
SNAPSHOT_KEYS = ("workspace", "commit", "committed_at", "references")
#: The business events a sensor may emit; each gets a flow that keeps it.
BUSINESS_EVENTS = ("registered", "paid", "bot", "support", "stage", "traffic")
#: A reading about something cards track (``crm_reading``).
READING_EVENT = "business.reading"
#: Refs of a reading event that are not reading fields: the link it is about, and the engine's own.
NOT_FIELDS = ("subject", "uid")
#: How often every open software card is read afresh and its stage checked (cron, UTC).
RECHECK_CRON = "*/20 * * * *"
STAGE_FLOW, RECHECK_FLOW = "crm_card_stage", "crm_card_stage_recheck"


def build(reg, db):
    @reg.step
    def record_crm_change(ctx):
        """Record a CRM revision reference in the workflow timeline without copying account fields."""
        required = ('org_id', 'record_id', 'revision')
        if any(not ctx.refs.get(key) for key in required):
            raise StepError('CRM event requires org_id, record_id and revision')
        return Done({key: ctx.refs[key] for key in required})

    @reg.step
    def hand_references_to_crm(ctx):
        """Give the CRM one workspace's whole set of record references (and roster) at one commit."""
        if any(key not in ctx.refs for key in SNAPSHOT_KEYS):
            raise StepError('workspace.references.changed carries workspace, commit, committed_at '
                            'and references', retryable=False)
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        from flows_steps.common import door_headers, http
        body = {key: ctx.refs[key] for key in SNAPSHOT_KEYS}
        if ctx.refs.get('members') is not None:
            body['members'] = ctx.refs['members']
        code, answer = http('POST', f'{base}/internal/references',
                            door_headers(), body, timeout=20)
        if code == 200 and isinstance(answer, dict):
            return Done({'workspace': body['workspace'], 'commit': body['commit'],
                         'status': answer.get('status'), 'references': answer.get('references')})
        raise StepError(f'the CRM answered {code} to a references snapshot', retryable=code >= 500 or code == 0)

    @reg.step
    def record_business_event(ctx):
        """Keep a business event a sensor read, with where it came from (needs its evidence). When the reading names
        a ``subject`` (an email: a person, by the CRM's match identifiers), the reading also lands on that record
        through the one reading route, exactly as a GitHub reading lands on a card (founder 1 Nov 2026, L2.7)."""
        ev = ctx.refs.get('_evidence')
        if not isinstance(ev, dict) or not ev.get('reading'):
            raise StepError('a business event carries _evidence with its reading', retryable=False)
        out = {'reading': ev['reading'], 'read_at': ev.get('read_at')}
        if str(ctx.refs.get('subject') or '').strip() and ev.get('read_at'):
            landed = _land_reading(ctx)
            if isinstance(landed, dict):
                out['records'] = landed.get('cards') or []
        return Done(out)

    def _land_reading(ctx):
        """The one reading route: the records that track the reading's subject (``/internal/cards/by-source``: a
        card's Source, or a person by email), then the reading onto each (``/internal/records/reading``)."""
        ev = ctx.refs.get('_evidence') or {}
        link = str(ctx.refs.get('subject') or '').strip()
        fields = {k: v for k, v in ctx.refs.items() if not k.startswith('_') and k not in NOT_FIELDS}
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return None
        from flows_steps.common import door_headers, http
        code, answer = http('POST', f'{base}/internal/cards/by-source', door_headers(), {'link': link}, timeout=20)
        if code != 200 or not isinstance(answer, dict):
            raise StepError(f'the CRM answered {code} to a by-source read', retryable=code >= 500 or code == 0)
        evidence = {'read_at': ev['read_at'], 'query': ev.get('query') or ev.get('path'), 'cursor': ev.get('cursor')}
        written = []
        for card in [str(c) for c in answer.get('cards') or []]:
            body = {'record': card, 'source': ev['reading'], 'fields': fields, 'evidence': evidence}
            code, out = http('POST', f'{base}/internal/records/reading', door_headers(), body, timeout=20)
            if code == 404:
                continue                      # the record went between the two calls, or keeps no readings
            if code != 200 or not isinstance(out, dict):
                raise StepError(f'the CRM answered {code} to a reading write',
                                retryable=code >= 500 or code == 0)
            written.append({'card': card, 'status': out.get('status'), 'revision': out.get('revision')})
        return {'subject': answer.get('subject') or link, 'cards': written}

    @reg.step
    def write_reading_to_crm(ctx):
        """Write a sensor reading onto every CRM card that tracks its subject. The reading's fields
        are its kept refs (``_``-prefixed and ``subject`` aside); the CRM writes only the declared
        ones and refuses the rest. Needs the reading's ``_evidence``."""
        ev = ctx.refs.get('_evidence')
        link = str(ctx.refs.get('subject') or '').strip()
        if not isinstance(ev, dict) or not ev.get('reading') or not ev.get('read_at') or not link:
            raise StepError('a reading event carries subject and _evidence with its reading and read_at',
                            retryable=False)
        fields = {k: v for k, v in ctx.refs.items() if not k.startswith('_') and k not in NOT_FIELDS}
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        from flows_steps.common import door_headers, http
        code, answer = http('POST', f'{base}/internal/cards/by-source', door_headers(), {'link': link}, timeout=20)
        if code != 200 or not isinstance(answer, dict):
            raise StepError(f'the CRM answered {code} to a by-source read', retryable=code >= 500 or code == 0)
        evidence = {'read_at': ev['read_at'], 'query': ev.get('query') or ev.get('path'), 'cursor': ev.get('cursor')}
        written = []
        for card in [str(c) for c in answer.get('cards') or []]:
            body = {'record': card, 'source': ev['reading'], 'fields': fields, 'evidence': evidence}
            code, out = http('POST', f'{base}/internal/records/reading', door_headers(), body, timeout=20)
            if code == 404:
                continue                      # the card went between the two calls: nothing to write
            if code != 200 or not isinstance(out, dict):
                raise StepError(f'the CRM answered {code} to a reading write',
                                retryable=code >= 500 or code == 0)
            written.append({'card': card, 'status': out.get('status'), 'revision': out.get('revision')})
        return Done({'subject': answer.get('subject') or link, 'reading': ev['reading'],
                     'read_at': ev['read_at'], 'cards': written})

    def _crm_post(base, path, body):
        from flows_steps.common import door_headers, http
        code, answer = http('POST', f'{base}{path}', door_headers(), body, timeout=20)
        if code != 200 or not isinstance(answer, dict):
            raise StepError(f'the CRM answered {code} to {path}', retryable=code >= 500 or code == 0)
        return answer

    def _put_stage(base, card, readings, mode):
        """Compute one card's stage and write it when it differs. Answers what was done. When the
        rule gives no stage (the card's Source was never read), the State is kept and the CRM records
        «no reading yet for <source>» once (``stage: null``); the same evidence again writes nothing."""
        from flows_defs import card_stage
        now = time.time()
        name, ev = card_stage.stage(readings, card_stage.claim_of(card, now))
        if name is None:
            held = card.get('stage_evidence') or {}
            if held.get('no_reading') and held.get('because') == ev['because'] and held.get('stage') is None:
                return {'card': card['id'], 'stage': None, 'status': 'unchanged', 'because': ev['because']}
            out = _crm_post(base, '/internal/cards/stage', {'record': card['id'], 'stage': None, 'because': ev['because'],
                                                            'facts': ev['facts'], 'mode': mode})
            return {'card': card['id'], 'stage': None, 'kept': out.get('stage'), 'was': card.get('state'),
                    'status': out.get('status'), 'because': ev['because']}
        if name == card.get('state'):
            return {'card': card['id'], 'stage': name, 'status': 'unchanged'}
        out = _crm_post(base, '/internal/cards/stage', {'record': card['id'], 'stage': name, 'because': ev['because'],
                                                        'facts': ev['facts'], 'mode': mode})
        return {'card': card['id'], 'stage': name, 'was': card.get('state'), 'status': out.get('status'),
                'because': ev['because']}

    @reg.step
    def card_stage_from_readings(ctx):
        """A record changed: if it is a software card, set its stage from its stored readings and a
        live Flows claim (``card_stage.stage``), only when it differs. Any other record: nothing."""
        from flows_defs import card_stage
        record = str(ctx.refs.get('record_id') or '').strip()
        if not record:
            raise StepError('crm.record.changed carries record_id', retryable=False)
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        cards = _crm_post(base, '/internal/cards/software', {'record': record}).get('cards') or []
        if not cards:
            return Done({'record': record, 'status': 'not_software'})
        card = cards[0]
        return Done(_put_stage(base, card, card_stage.readings_from_card(card), 'event'))

    @reg.step
    def recheck_card_stages(ctx):
        """Every open software card: read its delivery facts afresh (GET only), write them as its
        ``github/delivery`` reading, and correct its stage when the facts say another. A card that
        cannot be read is checked against what it already has, and the reason is in the receipt."""
        from flows_defs import card_stage
        from flows_sensors import reading as rk
        from flows_sensors.base import SensorFault
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        try:
            cfg = rk.load_config()
            spec = cfg.get('delivery') or {}
            conn = (cfg.get('connections') or {}).get(spec.get('connection') or '')
            if not conn or conn.get('transport') not in rk.TRANSPORTS:
                raise SensorFault('no delivery connection is declared', retryable=False)
        except SensorFault as e:
            spec, conn, why = None, None, str(e)
        else:
            why = ''
        cache = card_stage.PassCache()
        done = []
        for card in _crm_post(base, '/internal/cards/software', {}).get('cards') or []:
            row = {'card': card['id']}
            subject = card_stage.subject_of(card.get('source'))
            if conn is not None and subject:
                now = time.time()
                try:
                    fields = card_stage.read_delivery(rk.http_get, conn, spec, subject, card.get('source'), cache)
                except (card_stage.ReadFault, SensorFault) as e:
                    row['read'] = f'not read: {e}'
                else:
                    evidence = {'read_at': now, 'query': f'delivery {subject}'}
                    _crm_post(base, '/internal/records/reading', {'record': card['id'], 'source': card_stage.DELIVERY_SOURCE,
                                                                  'fields': fields, 'evidence': evidence})
                    reading = dict(card.get('reading') or {})
                    sources = dict(reading.get('sources') or {})
                    sources[card_stage.DELIVERY_SOURCE] = {'fields': fields,
                                                            'evidence': {'source': card_stage.DELIVERY_SOURCE, **evidence}}
                    card = {**card, 'reading': {**reading, 'sources': sources}}
                    row['read'] = 'read'
            else:
                row['read'] = f'not read: {why or "the card tracks no GitHub link"}'
            readings = card_stage.readings_from_card(card)
            if row['read'] != 'read':
                readings['unread'] = row['read']
            row.update(_put_stage(base, card, readings, 'recheck'))
            done.append(row)
        return Done({'cards': len(done), 'corrected': [r for r in done if r.get('status') in ('corrected', 'restored')],
                     'unread': sum(1 for r in done if r['read'] != 'read')})

    for kind in BUSINESS_EVENTS:
        reg.flow(name=f'business_{kind}', version=1, on=EventType(f'business.{kind}'),
                 steps=[reg.steps['record_business_event']])
    reg.flow(name='crm_reading', version=1, on=EventType(READING_EVENT),
             steps=[reg.steps['write_reading_to_crm']])
    reg.flow(name='crm_change_log', version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['record_crm_change']])
    reg.flow(name=STAGE_FLOW, version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['card_stage_from_readings']])
    # a SCHEDULE flow of the image (registry.Flow with a cron), filed the way flow_by_names files one,
    # but not as a DB row: a refresh from the DB never retires it
    from flows import Flow
    from flows.registry import SCHEDULE_EVENT
    recheck = Flow(name=RECHECK_FLOW, version=1, on=EventType(SCHEDULE_EVENT),
                   steps=('recheck_card_stages',), cron=RECHECK_CRON)
    reg.flows[(recheck.ident, recheck.version)] = recheck
    reg.scheduled.setdefault(recheck.ident, []).append(recheck)
    reg.flow(name='crm_references', version=1, on=EventType('workspace.references.changed'),
             steps=[reg.steps['hand_references_to_crm']])
