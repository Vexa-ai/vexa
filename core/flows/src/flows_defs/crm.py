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
* ``card_due_alarms`` puts a card's Due into its owner's stream (the card's ``Waiting``, through
  ``to_stream``): ``card.due_soon`` one working day before, ``card.due`` on the day. Its keyed schedule
  (one row per alarm per card) is kept by ``card_due_arm`` (``crm.record.changed``: a moved Due moves
  it, a cleared Due or a closed card cancels it) and ``card_due_recheck`` (hourly). Both read the cards
  through ``/internal/cards/due``; the refs are the card id, the due date and the owner.
"""
import json
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
#: A card's Due (the date we told someone) as two alarms in its owner's stream: ``card.due_soon`` one
#: working day before, ``card.due`` on the day (UTC midnights). ``card_due_arm`` keeps the keyed schedule
#: true on every record change; ``card_due_recheck`` re-reads every open card with a Due each hour, so an
#: alarm the worker was down for fires late instead of never, and one whose card went is cancelled.
DUE_FLOW, DUE_ARM_FLOW, DUE_RECHECK_FLOW = "card_due_alarms", "card_due_arm", "card_due_recheck"
DUE_RECHECK_CRON = "7 * * * *"
#: a card change its owner acts on (``card_change_to_owner``), into the owner's stream
CHANGE_FLOW = "card_change_to_owner"
CHANGE_EVENT = "card.changed"
#: who owns the sources (the Data tab's connections): their failures go to its stream (``source.failing``)
SOURCES_OWNER = "factory"
SOURCE_FAULTS_FLOW = "source_faults_to_owner"
SOURCE_FAULTS_CRON = "*/10 * * * *"
SOURCE_EVENT = "source.failing"
DUE_EVENT, DUE_SOON_EVENT = "card.due", "card.due_soon"


def due_times(due) -> "tuple[float, float] | None":
    """A ``Due`` date (``YYYY-MM-DD``) → (due_soon_at, due_at): the previous working day's midnight and the
    due date's, UTC. A Monday's working day before is the Friday. ``None`` for anything not a date."""
    from datetime import date, datetime, timedelta, timezone
    try:
        d = date.fromisoformat(str(due or "")[:10])
    except ValueError:
        return None
    soon = d - timedelta(days=1)
    while soon.weekday() >= 5:
        soon -= timedelta(days=1)
    at = lambda day: datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()  # noqa: E731
    return at(soon), at(d)


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
            pin_name = (spec.get('prod_pin') or {}).get('connection')
            pin_conn = (cfg.get('connections') or {}).get(pin_name) if pin_name else None
            if pin_name and (not pin_conn or pin_conn.get('transport') not in rk.TRANSPORTS):
                raise SensorFault('the prod pin names an undeclared connection', retryable=False)
        except SensorFault as e:
            spec, conn, pin_conn, why = None, None, None, str(e)
        else:
            why = ''
        cache = card_stage.PassCache()
        done = []
        for card in _crm_post(base, '/internal/cards/software', {}).get('cards') or []:
            row = {'card': card['id']}
            subject = card_stage.subject_of(card.get('source'))
            repos = {str(x).lower() for x in (spec or {}).get('repos') or []}
            repo = subject.split('#', 1)[0].split('/', 1)[-1] if subject else ''
            if conn is not None and subject and repos and repo not in repos:
                row['read'] = f'not read: {repo} is outside the declared repos'   # not this connection's to read
            elif conn is not None and subject:
                now = time.time()
                try:
                    fields = card_stage.read_delivery(rk.http_get, conn, spec, subject, card.get('source'), cache,
                                                      pin_conn)
                except card_stage.PinFault as e:
                    row['read'] = f'not read: prod pin: {e}'
                except (card_stage.ReadFault, SensorFault) as e:
                    row['read'] = f'not read: {e}'
                    row['failed'] = True
                    if getattr(e, 'status', None):
                        row['refused'] = e.status                 # the source's own answer, per card
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
        # pull requests no card tracks (the stage rule can attach no fact to them): facts, never a failure
        unlinked, unlinked_fault = [], None
        if conn is not None and spec.get('repos'):
            def cards_for(link):
                return _crm_post(base, '/internal/cards/by-source', {'link': link}).get('cards') or []
            try:
                unlinked = card_stage.unlinked_prs(rk.http_get, conn, spec, cache, cards_for, time.time(), pin_conn)
            except (card_stage.ReadFault, SensorFault) as e:
                unlinked_fault = str(e)
        # each reading's own failures (founder 1 Nov: a source's state comes from its own latest reads): the card's
        # GitHub reads that failed, and the pin's one read this pass; a card with no GitHub link is no failure
        name = (spec or {}).get('connection') or 'github'
        pin_name = ((spec or {}).get('prod_pin') or {}).get('connection') or name
        failed = {f'{name}/delivery': sum(1 for r in done if r.get('failed') or (conn is None and r['read'] != 'read')),
                  f'{pin_name}/prod-pin': 1 if cache.pin_fault is not None else 0}
        import hashlib
        for rid, n, why in ((f'{name}/delivery', failed[f'{name}/delivery'], why or 'card reads failed'),
                            (f'{pin_name}/prod-pin', failed[f'{pin_name}/prod-pin'], cache.pin_fault or '')):
            if n:
                key = f"reading:{rid}:{hashlib.sha256(str(why).encode()).hexdigest()[:12]}"
                _to_stream(ctx, SOURCES_OWNER, key, SOURCE_EVENT,
                           {'reading': rid, 'status': 'failing', 'failures': n, 'reason': str(why)[:300]})
        return Done({'cards': len(done), 'corrected': [r for r in done if r.get('status') in ('corrected', 'restored')],
                     'unread': sum(1 for r in done if r['read'] != 'read'), 'failed': failed,
                     'refused': [{'card': r['card'], 'status': r['refused']} for r in done if r.get('refused')][:50],
                     'pin_read': cache.pin is not None or cache.pin_fault is not None,
                     'unlinked': unlinked, **({'unlinked_fault': unlinked_fault} if unlinked_fault else {})})

    class _At:
        def __init__(self, t):
            self.t = t

        def now(self):
            return self.t

    def _arm_due(flow, card, clock):
        """Arm, move or cancel one card's two alarms. Refs: card id, due date, owner — nothing else."""
        from flows import keyed, stream
        cid = str(card.get('id') or '')
        times = due_times(card.get('due')) if card.get('open') else None
        owner = str(card.get('owner') or '').strip()
        if times is None or not stream.RECIPIENT.match(owner):
            gone = [k for k in (f'{cid}:soon', f'{cid}:due') if keyed.cancel(db, clock, flow, key=k)]
            return {'card': cid, 'status': 'cancelled' if gone else 'none'}
        soon_at, due_at = times
        payload = {'card': cid, 'due': str(card['due'])[:10], 'owner': owner}
        # one row per alarm per card, bounded by the cards themselves: no per-person cap applies
        out = {'card': cid, 'due': keyed.upsert(db, clock, flow, key=f'{cid}:due', fire_at=due_at,
                                                event_type=DUE_EVENT, payload=payload, per_user=-1)}
        if clock.now() < due_at:
            out['soon'] = keyed.upsert(db, clock, flow, key=f'{cid}:soon', fire_at=soon_at,
                                       event_type=DUE_SOON_EVENT, payload=payload, per_user=-1)
        else:
            keyed.cancel(db, clock, flow, key=f'{cid}:soon')
            out['soon'] = 'past'
        return out

    @reg.step
    def arm_card_due(ctx):
        """Keep a card's due alarms true to its Due: ``card.due_soon`` one working day before and ``card.due``
        on the day, into the card's owner's stream (its Waiting). A record change re-reads that card; the
        hourly run re-reads every open card with a Due and cancels the alarms of any card no longer among
        them. Reads: refs.record_id (absent on the hourly run) · Effect: the keyed schedule of
        ``card_due_alarms`` · Result: {cards, armed}."""
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        flow = reg.newest('instance', DUE_FLOW)
        if flow is None:
            return Done({'status': 'no_flow'})
        clock = _At(ctx.clock_now)
        record = str(ctx.refs.get('record_id') or '').strip()
        cards = _crm_post(base, '/internal/cards/due', {'record': record} if record else {}).get('cards') or []
        if record and not cards:
            cards = [{'id': record, 'open': False}]       # the record is gone, or is not a card
        done = [_arm_due(flow, card, clock) for card in cards]
        if not record:
            live = {str(c.get('id')) for c in cards}
            for (key,) in db.execute("SELECT key FROM keyed_schedule WHERE scope = 'instance' AND flow = :f "
                                     "AND status = 'armed'", {'f': DUE_FLOW}):
                if key.rsplit(':', 1)[0] not in live:
                    done.append(_arm_due(flow, {'id': key.rsplit(':', 1)[0], 'open': False}, clock))
        return Done({'cards': len(cards), 'armed': [d for d in done if d.get('status') != 'none']})

    def _role_uids():
        """``VEXA_FLOWS_STREAM_OWNERS``: a role → the subject whose credential reads its stream."""
        try:
            raw = json.loads(flows_config.get('VEXA_FLOWS_STREAM_OWNERS') or '{}')
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def _to_stream(ctx, recipient, source_event_id, event_type, refs):
        from flows import stream
        try:
            return stream.append(db, getattr(ctx, 'clock_now', None) or time.time(), recipient=recipient,
                                 source_event_id=source_event_id, event_type=event_type, refs=refs,
                                 reaction_id=getattr(ctx, 'reaction_id', '') or '')
        except stream.StreamError as e:
            return {'recipient': recipient, 'status': 'refused', 'note': str(e)}

    @reg.step
    def card_change_to_owner(ctx):
        """A card change its owner acts on, into the owner's stream (its ``Waiting``; a card waiting on the
        founder goes to the ceo, the one way to him): its stage, its claim, its Due, Started, a new card, or a
        new owner (both owners are told). A change the owner itself wrote is not echoed back; a revision that
        changed none of these (a reading) appends nothing. Reads: refs.record_id, refs.revision · CRM
        ``/internal/cards/change`` · Effect: one ``card.changed`` item per owner, once per change."""
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        record, rev = str(ctx.refs.get('record_id') or ''), ctx.refs.get('revision')
        if not record or not str(rev or '').isdigit():
            raise StepError('crm.record.changed carries record_id and revision', retryable=False)
        ch = _crm_post(base, '/internal/cards/change', {'record': record, 'revision': int(rev)})
        if not ch.get('card') or not ch.get('changed'):
            return Done({'status': 'none'})
        uids = _role_uids()
        owners = [ch.get('owner')] + ([ch.get('was_owner')] if 'Waiting' in ch['changed'] else [])
        refs = {'card': ch['card'], 'revision': ch.get('revision'), 'changed': ch['changed'], 'state': ch.get('state'),
                'owner': ch.get('owner'), 'by': ch.get('actor'), **({'created': True} if ch.get('created') else {})}
        out = []
        for who in dict.fromkeys(str(o) for o in owners if o):
            to = 'ceo' if who == 'founder' else who
            if uids.get(to) and uids[to] == str(ch.get('actor') or ''):
                out.append({'recipient': to, 'status': 'own_write'})
                continue
            out.append(_to_stream(ctx, to, ctx.reaction.source_event_id, CHANGE_EVENT, refs))
        return Done({'card': ch['card'], 'appended': out})

    @reg.step
    def source_faults_to_owner(ctx):
        """Every source reading that is failing, into the sources' owner's stream (``SOURCES_OWNER``): a sensor
        degraded or stopped, or with failures. Once per sensor and failure (its status and reason), so a fault
        that stays is told once and a new reason is told again. Reads: the sensor table · Effect: one
        ``source.failing`` item per new fault."""
        import hashlib
        rows = db.execute("SELECT scope, name, connection_id, status, failures, reason FROM sensor "
                          "WHERE status IN ('degraded', 'stopped') OR (status = 'active' AND failures > 0)")
        out = []
        for scope, name, conn, status, failures, reason in rows:
            why = str(reason or '')[:300]
            key = f"sensor:{scope}:{name}:{status}:{hashlib.sha256(why.encode()).hexdigest()[:12]}"
            out.append(_to_stream(ctx, SOURCES_OWNER, key, SOURCE_EVENT,
                                  {'reading': conn or name, 'sensor': name, 'status': status,
                                   'failures': int(failures or 0), 'reason': why}))
        return Done({'failing': len(rows), 'appended': [o for o in out if o.get('status') == 'appended']})

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
    # the due alarms: armed by a record change or the hourly re-read, fired by the keyed schedule into
    # `to_stream` (refs.owner is the card's Waiting)
    from flows.keyed import KEYED_EVENT
    from flows_steps import stream as stream_steps
    reg.flow(name=DUE_FLOW, version=1, on=EventType(KEYED_EVENT), steps=[stream_steps.register(reg, db)])
    reg.flow(name=DUE_ARM_FLOW, version=1, on=EventType('crm.record.changed'), steps=[reg.steps['arm_card_due']])
    due_recheck = Flow(name=DUE_RECHECK_FLOW, version=1, on=EventType(SCHEDULE_EVENT),
                       steps=('arm_card_due',), cron=DUE_RECHECK_CRON)
    reg.flows[(due_recheck.ident, due_recheck.version)] = due_recheck
    reg.scheduled.setdefault(due_recheck.ident, []).append(due_recheck)
    reg.flow(name=CHANGE_FLOW, version=1, on=EventType('crm.record.changed'), steps=[reg.steps['card_change_to_owner']])
    faults = Flow(name=SOURCE_FAULTS_FLOW, version=1, on=EventType(SCHEDULE_EVENT),
                  steps=('source_faults_to_owner',), cron=SOURCE_FAULTS_CRON)
    reg.flows[(faults.ident, faults.version)] = faults
    reg.scheduled.setdefault(faults.ident, []).append(faults)
    reg.flow(name='crm_references', version=1, on=EventType('workspace.references.changed'),
             steps=[reg.steps['hand_references_to_crm']])
