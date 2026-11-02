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
import re
from datetime import datetime, timedelta, timezone
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
#: the board sense check (founder 2 Nov ~11:10Z): a role reads its views of the board (/api/view) and posts each row that
#: does not make sense as one ``factory.sense`` fact; it reaches that role's stream as ``sense.finding``, once per finding
SENSE_FLOW, SENSE_FACT, SENSE_EVENT = "sense_to_owner", "factory.sense", "sense.finding"
SENSE_ROLES = ("ceo", "software", "factory", "business", "support")
SENSE_REASONS = ("done_not_in_main", "stage_vs_facts", "duplicate", "superseded_open", "unlinked_pr", "running_empty",
                 "stale_due", "lapsed_hold", "unlinked")
#: the card heartbeat (founder 2 Nov ~09:30Z): each open card's keyed next check; a check that finds no new fact,
#: Next or State is a missed beat — the 1st to its owner, the 2nd to the manager, the 3rd and on to the ceo
BEAT_FLOW, BEAT_ARM_FLOW, BEAT_RECHECK_FLOW = "card_heartbeat", "card_heartbeat_arm", "card_heartbeat_recheck"
BEAT_RECHECK_CRON = "17 * * * *"
BEAT_EVENT = "card.beat_missed"
BEAT_MANAGER = "factory"
#: support cards (founder 2 Nov ~09:00Z): the rule over the support readings, and its SLA alarms (keyed, into the
#: support stream through card_due_alarms' to_stream)
SUPPORT_FLOWS = {"support_card": "business.support", "support_card_issue": "business.reading"}
SUPPORT_REMIND_EVENT, SUPPORT_DUE_EVENT = "support.remind", "support.due"
OUTSIDE = ("NONE", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN")
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
        owner = 'ceo' if owner == 'founder' else owner        # only the ceo brings anything to the founder
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
    def sense_to_owner(ctx):
        """A board row that does not make sense (``factory.sense``: the role whose view it is in, the row id, the reason
        code, the view), into that role's stream as ``sense.finding``. Once per fact (its source_event_id names the role,
        the row, the reason and the day). Reads: refs · Effect: one ``sense.finding`` item."""
        r = ctx.refs
        role, row, reason = str(r.get('role') or ''), str(r.get('row') or ''), str(r.get('reason') or '')
        if role not in SENSE_ROLES or reason not in SENSE_REASONS or not row:
            raise StepError(f"factory.sense carries role in {SENSE_ROLES}, reason in {SENSE_REASONS} and a row",
                            retryable=False)
        refs = {'row': row[:120], 'reason': reason, 'view': str(r.get('view') or '')[:40],
                **({'with': str(r['with'])[:120]} if r.get('with') else {}),
                **({'fact': str(r['fact'])[:300]} if r.get('fact') else {})}
        return Done({'appended': [_to_stream(ctx, role, ctx.reaction.source_event_id, SENSE_EVENT, refs)]})

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

    def _add_hours_or_days(t, sla):
        """``t`` (epoch) plus the SLA: hours, or working days (Mon–Fri) for ``*_bd``."""
        if sla.get('due_bd'):
            n, d = int(sla['due_bd']), datetime.fromtimestamp(t, timezone.utc)
            while n:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    n -= 1
            return d.timestamp()
        return t + float(sla.get('due_h') or 0) * 3600

    def _support_message(ctx, cfg, kind):
        """What one support reading's event says: {source, title, sender, at, ours, customers_only} or None."""
        ev = ctx.refs.get('_evidence') or {}
        reading, r = str(ev.get('reading') or ''), ctx.refs
        read_at = float(ev.get('read_at') or ctx.clock_now)
        if kind == 'discord':
            root = str(r.get('reply_to') or r.get('message') or '')
            if not root.isdigit() or not str(r.get('channel') or '').isdigit():
                return None
            at = _iso_epoch(r.get('at')) or read_at
            ours = str(r.get('from') or '') in ((cfg.get('ours') or {}).get('discord') or [])
            if ours and not r.get('reply_to'):
                return None                                   # our own new post is no ticket
            return {'source': f"https://discord.com/channels/{cfg.get('discord_guild')}/{r['channel']}/{root}",
                    'title': f"{reading} · {r.get('from') or '—'}", 'sender': '', 'at': at, 'ours': ours, 'sla': 'discord'}
        if kind in ('email', 'email_customers'):
            thread, sender = str(r.get('thread') or ''), str(r.get('from') or '')
            if not thread.isalnum():
                return None
            mail = sender.rsplit('<', 1)[-1].rstrip('>').strip().lower()
            ours = mail.rsplit('@', 1)[-1] in ((cfg.get('ours') or {}).get('email_domains') or [])
            return {'source': f'https://mail.google.com/mail/u/0/#all/{thread}', 'title': f'{reading} · {sender[:120]}',
                    'sender': sender, 'at': read_at, 'ours': ours, 'customers_only': kind == 'email_customers',
                    'sla': 'email'}
        return None

    def _iso_epoch(v):
        try:
            return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
        except ValueError:
            return None

    def _github_message(ctx, cfg):
        """A GitHub issue opened by someone outside the org: its last word decides (a member's comment after the
        outsider's last one, or the issue closed, is our reply). GET only, through the delivery connection."""
        from flows_sensors import reading as rk
        r = ctx.refs
        m = re.match(r'^https://github\.com/([\w.\-]+)/([\w.\-]+)/issues/(\d+)$', str(r.get('url') or r.get('subject') or ''))
        if not m:
            return None
        rcfg = rk.load_config()
        conn = (rcfg.get('connections') or {}).get((rcfg.get('delivery') or {}).get('connection') or 'github')
        issue = rk.http_get(conn, f'/repos/{m.group(1)}/{m.group(2)}/issues/{m.group(3)}', {})
        if not isinstance(issue, dict) or str(issue.get('author_association')) not in OUTSIDE:
            return None
        comments = rk.http_get(conn, f'/repos/{m.group(1)}/{m.group(2)}/issues/{m.group(3)}/comments', {'per_page': '100'})
        comments = [c for c in comments if isinstance(c, dict)] if isinstance(comments, list) else []
        last_out = max([_iso_epoch(issue.get('created_at')) or 0] + [_iso_epoch(c.get('created_at')) or 0 for c in comments
                                                                     if str(c.get('author_association')) in OUTSIDE])
        last_us = max([0] + [_iso_epoch(c.get('created_at')) or 0 for c in comments
                             if str(c.get('author_association')) not in OUTSIDE])
        ours = issue.get('state') == 'closed' or last_us > last_out
        return {'source': issue.get('html_url'), 'title': str(issue.get('title') or '')[:200],
                'sender': '', 'at': last_us if ours else last_out, 'ours': ours, 'sla': 'github'}

    def _beat_to(misses, owner):
        owner = 'ceo' if owner in (None, '', 'founder') else owner
        return owner if misses == 1 else (BEAT_MANAGER if misses == 2 else 'ceo')

    def _arm_beat(flow, card, clock, payload=None, at=None):
        from flows import keyed
        key = f"{card['id']}:beat"
        if not card.get('open'):
            return {'card': card['id'], 'status': 'cancelled' if keyed.cancel(db, clock, flow, key=key) else 'none'}
        when = at if at is not None else _iso_epoch(card.get('next_check_at'))
        if when is None or when <= clock.now():
            when = clock.now() + float(card.get('every_s') or 86400)
        body = payload or {'card': card['id'], 'mark': card.get('mark'), 'misses': 0}
        return {'card': card['id'], 'status': keyed.upsert(db, clock, flow, key=key, fire_at=when, event_type=BEAT_EVENT,
                                                           payload=body, per_user=-1)}

    @reg.step
    def arm_card_beat(ctx):
        """Keep each open card's heartbeat armed: a card change arms a card that has no beat yet (a new card gets
        its first beat at creation) and cancels a closed card's; the hourly run arms every open card that has none
        (or whose beat was missed while the worker was down) and cancels the beats of cards that are gone. An
        armed beat is never moved by a change: the beat itself compares what changed. Reads: refs.record_id
        (absent hourly) · CRM ``/internal/cards/beat`` · Effect: the keyed schedule of ``card_heartbeat``."""
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        flow = reg.newest('instance', BEAT_FLOW)
        if flow is None:
            return Done({'status': 'no_flow'})
        clock = _At(ctx.clock_now)
        record = str(ctx.refs.get('record_id') or '').strip()
        cards = _crm_post(base, '/internal/cards/beat', {'record': record} if record else {}).get('cards') or []
        if record and not cards:
            cards = [{'id': record, 'open': False}]
        armed = {k for (k,) in db.execute("SELECT key FROM keyed_schedule WHERE scope = 'instance' AND flow = :f "
                                          "AND status = 'armed'", {'f': BEAT_FLOW})}
        done = [_arm_beat(flow, c, clock) for c in cards if not c.get('open') or f"{c['id']}:beat" not in armed]
        if not record:
            live = {f"{c['id']}:beat" for c in cards}
            done += [_arm_beat(flow, {'id': k.rsplit(':', 1)[0], 'open': False}, clock) for k in armed - live]
        return Done({'cards': len(cards), 'armed': [d for d in done if d.get('status') not in ('none', 'unchanged')]})

    @reg.step
    def card_beat(ctx):
        """One card's heartbeat: read it again; a new fact, Next or State since the last beat resets the count;
        none is a missed beat, told to the owner (1st), the manager (2nd), then the ceo (3rd and on), as
        ``card.beat_missed`` with the card, its owner and the count. Re-arms the next beat one check interval on
        (or at its Due when sooner); a closed or gone card stops beating. Reads: refs.card, refs.mark,
        refs.misses · Effect: at most one stream item; the next beat."""
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        flow, clock = reg.newest('instance', BEAT_FLOW), _At(ctx.clock_now)
        cid = str(ctx.refs.get('card') or '')
        cards = _crm_post(base, '/internal/cards/beat', {'record': cid}).get('cards') or []
        card = cards[0] if cards else {'id': cid, 'open': False}
        if not card.get('open'):
            return Done({'card': cid, 'status': 'closed'})
        misses = 0 if card.get('mark') != ctx.refs.get('mark') else int(ctx.refs.get('misses') or 0) + 1
        out = {'card': cid, 'misses': misses}
        if misses:
            to = _beat_to(misses, card.get('owner'))
            out['told'] = _to_stream(ctx, to, ctx.reaction.source_event_id, BEAT_EVENT,
                                     {'card': cid, 'owner': card.get('owner'), 'misses': misses, 'state': card.get('state')})
        nxt = ctx.clock_now + float(card.get('every_s') or 86400)
        due = _iso_epoch(card.get('next_check_at'))
        if due is not None and ctx.clock_now < due < nxt:
            nxt = due
        if flow is not None:
            out['next'] = _arm_beat(flow, card, clock, {'card': cid, 'mark': card.get('mark'), 'misses': misses}, nxt)
        return Done(out)

    @reg.step
    def support_card(ctx):
        """An inbound support message → the ONE card of its thread (CRM ``/internal/cards/support/inbound``: no
        Kind, Waiting=support, About = the sender's Person, Due = the channel SLA's date) and its SLA alarms into
        the support stream (a reminder, then the deadline, at the exact times); our reply seen in the thread →
        Done (``…/reply``) and its alarms cancelled. Which readings count, what is ours and the SLAs (with their
        doctrine lines) are the ``support`` block of the connections file. Reads: the reading's refs · Effect: a
        support card made, reopened or closed; two keyed alarms."""
        from flows_sensors import reading as rk
        from flows import keyed
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        cfg = rk.load_config().get('support') or {}
        reading = str((ctx.refs.get('_evidence') or {}).get('reading') or '')
        kind = (cfg.get('readings') or {}).get(reading)
        if not kind:
            return Done({'status': 'not_support', 'reading': reading})
        msg = _github_message(ctx, cfg) if kind == 'github' else _support_message(ctx, cfg, kind)
        if not msg:
            return Done({'status': 'not_a_ticket', 'reading': reading})
        at_iso = datetime.fromtimestamp(msg['at'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        flow, clock = reg.newest('instance', DUE_FLOW), _At(ctx.clock_now)
        if msg['ours']:
            out = _crm_post(base, '/internal/cards/support/reply', {'source': msg['source'], 'at': at_iso})
            if out.get('card') and flow is not None:
                for k in ('sla_remind', 'sla_due'):
                    keyed.cancel(db, clock, flow, key=f"{out['card']}:{k}")
            return Done({'reading': reading, **out})
        sla = dict((cfg.get('sla') or {}).get(msg['sla']) or {})
        body = {'source': msg['source'], 'title': msg['title'] or msg['source'], 'sender': msg['sender'], 'at': at_iso,
                'customers_only': bool(msg.get('customers_only'))}
        due_at = _add_hours_or_days(msg['at'], sla) if sla else None
        if due_at:
            body['due'] = datetime.fromtimestamp(due_at, timezone.utc).strftime('%Y-%m-%d')
        out = _crm_post(base, '/internal/cards/support/inbound', body)
        if out.get('status') in ('created', 'reopened') and out.get('card') and due_at and flow is not None:
            if msg['sla'] == 'email' and out.get('customer'):
                # a customer's email keeps the customer SLA's alarms (the CRM said who it is); the card's Due date
                # was the email SLA's, which is the same day or later only across a weekend
                sla = dict((cfg.get('sla') or {}).get('email_customer') or sla)
                due_at = _add_hours_or_days(msg['at'], sla)
            payload = {'card': out['card'], 'owner': 'support', 'due_at': datetime.fromtimestamp(due_at, timezone.utc)
                       .strftime('%Y-%m-%dT%H:%M:%SZ')}
            keyed.upsert(db, clock, flow, key=f"{out['card']}:sla_due", fire_at=due_at, event_type=SUPPORT_DUE_EVENT,
                         payload=payload, per_user=-1)
            if sla.get('remind_h'):
                keyed.upsert(db, clock, flow, key=f"{out['card']}:sla_remind",
                             fire_at=msg['at'] + float(sla['remind_h']) * 3600, event_type=SUPPORT_REMIND_EVENT,
                             payload=payload, per_user=-1)
        return Done({'reading': reading, **out})

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
    reg.flow(name=BEAT_FLOW, version=1, on=EventType(KEYED_EVENT), steps=[reg.steps['card_beat']])
    reg.flow(name=BEAT_ARM_FLOW, version=1, on=EventType('crm.record.changed'), steps=[reg.steps['arm_card_beat']])
    beat_recheck = Flow(name=BEAT_RECHECK_FLOW, version=1, on=EventType(SCHEDULE_EVENT),
                        steps=('arm_card_beat',), cron=BEAT_RECHECK_CRON)
    reg.flows[(beat_recheck.ident, beat_recheck.version)] = beat_recheck
    reg.scheduled.setdefault(beat_recheck.ident, []).append(beat_recheck)
    for name, event in SUPPORT_FLOWS.items():
        reg.flow(name=name, version=1, on=EventType(event), steps=[reg.steps['support_card']])
    reg.flow(name=CHANGE_FLOW, version=1, on=EventType('crm.record.changed'), steps=[reg.steps['card_change_to_owner']])
    reg.flow(name=SENSE_FLOW, version=1, on=EventType(SENSE_FACT), steps=[reg.steps['sense_to_owner']])
    faults = Flow(name=SOURCE_FAULTS_FLOW, version=1, on=EventType(SCHEDULE_EVENT),
                  steps=('source_faults_to_owner',), cron=SOURCE_FAULTS_CRON)
    reg.flows[(faults.ident, faults.version)] = faults
    reg.scheduled.setdefault(faults.ident, []).append(faults)
    reg.flow(name='crm_references', version=1, on=EventType('workspace.references.changed'),
             steps=[reg.steps['hand_references_to_crm']])
