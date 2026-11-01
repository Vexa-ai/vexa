"""Optional CRM pack, enabled through VEXA_FLOWS_DEFS_EXTRA=flows_defs.crm (ADR-0047).

Two flows, both carrying references and never account fields:

* ``crm_change_log`` records a CRM revision reference (``crm.record.changed``) in the timeline.
* ``crm_references`` hands a workspace's links to CRM records at one commit
  (``workspace.references.changed``, published by agent-api) to the CRM, which replaces that
  workspace's set. The fact is a full snapshot, so a retry or a redelivery changes nothing.

A fact grants no record access. With no CRM deployed (``VEXA_FLOWS_CRM_API_URL`` unset) the second
flow ends ``not_present`` rather than knocking on a missing door.

* ``business_<kind>`` (one per kind in ``BUSINESS_EVENTS``) keeps a business event a sensor read
  (``business.registered|paid|bot|support|stage``, from the generic ``reading`` sensor kind) as
  one reaction: the event's durable row, with its ``_evidence`` (connection, reading, path,
  read_at).
* ``crm_reading`` (``business.reading``) writes a reading onto the CRM cards it is about: the
  event's ``subject`` is a link (a GitHub issue or pull request URL, from the ``github`` connection's
  readings); the CRM answers which cards track it (``/internal/cards/by-source``: their typed
  ``Source``, or a legacy «GitHub: owner/repo#N» in ``Summary``), and the step writes the reading's
  declared fields with its evidence onto each (``/internal/records/reading``) as the card's
  ``Reading`` — never a field a person or an agent writes. The CRM keeps it idempotent by (record,
  source, read_at), so a retry or a redelivery changes nothing.
"""
import flows_config
from flows import Done, EventType, NotPresent, StepError

CRM_URL_ENV = "VEXA_FLOWS_CRM_API_URL"
SNAPSHOT_KEYS = ("workspace", "commit", "committed_at", "references")
#: The business events a sensor may emit; each gets a flow that keeps it.
BUSINESS_EVENTS = ("registered", "paid", "bot", "support", "stage")
#: A reading about something cards track (``crm_reading``).
READING_EVENT = "business.reading"
#: Refs of a reading event that are not reading fields: the link it is about, and the engine's own.
NOT_FIELDS = ("subject", "uid")


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
        """Keep a business event a sensor read, with where it came from. Needs its evidence."""
        ev = ctx.refs.get('_evidence')
        if not isinstance(ev, dict) or not ev.get('reading'):
            raise StepError('a business event carries _evidence with its reading', retryable=False)
        return Done({'reading': ev['reading'], 'read_at': ev.get('read_at')})

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

    for kind in BUSINESS_EVENTS:
        reg.flow(name=f'business_{kind}', version=1, on=EventType(f'business.{kind}'),
                 steps=[reg.steps['record_business_event']])
    reg.flow(name='crm_reading', version=1, on=EventType(READING_EVENT),
             steps=[reg.steps['write_reading_to_crm']])
    reg.flow(name='crm_change_log', version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['record_crm_change']])
    reg.flow(name='crm_references', version=1, on=EventType('workspace.references.changed'),
             steps=[reg.steps['hand_references_to_crm']])
