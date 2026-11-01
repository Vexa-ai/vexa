"""Optional CRM pack, enabled through VEXA_FLOWS_DEFS_EXTRA=flows_defs.crm (ADR-0047).

Three flows, all carrying references and never account fields:

* ``crm_change_log`` records a CRM revision reference (``crm.record.changed``) in the timeline.
* ``crm_references`` hands a workspace's links to CRM records at one commit
  (``workspace.references.changed``, published by agent-api) to the CRM, which replaces that
  workspace's set. The fact is a full snapshot, so a retry or a redelivery changes nothing.
* ``card_dependents_wake`` (``crm.record.changed``) wakes the reactions sleeping in
  ``card_blockers_done`` on the card that changed, so they re-check at once.

And one step any flow may put first, ``card_blockers_done``: it keeps a reaction about a card asleep
while that card waits on other cards (its ``Chain.blocked_by`` in the CRM), and lets it go on when
every one of them is finished. The link is the CRM's fact; Flows only enforces it in time — it holds
the ids it is waiting on in its scratch and nothing about the cards themselves.

A fact grants no record access. With no CRM deployed (``VEXA_FLOWS_CRM_API_URL`` unset) the CRM-calling
steps end ``not_present`` rather than knocking on a missing door.
"""
import json

import flows_config
from flows import Done, EventType, NotPresent, StepError, Wait, wake

CRM_URL_ENV = "VEXA_FLOWS_CRM_API_URL"
SNAPSHOT_KEYS = ("workspace", "commit", "committed_at", "references")
#: The step that sleeps on a card's blockers, and the one place its name is spelled for the wake.
BLOCKERS_STEP = "card_blockers_done"
#: How long a reaction sleeps before it asks the CRM again on its own. The wake on
#: ``crm.record.changed`` is what moves it; this re-check only covers a change whose fact never
#: reached flows (a delivery lost in transit) or landed while the step itself was running.
#: A flow may set its own with the param ``blockers_recheck_s``.
BLOCKERS_RECHECK_S = 6 * 3600


def card_of(refs) -> str:
    """The CRM card a reaction is about: ``refs.card``, or ``refs.ref`` written ``card:<id>``."""
    card = str(refs.get("card") or "").strip()
    if card:
        return card
    ref = str(refs.get("ref") or "").strip()
    return ref[len("card:"):].strip() if ref.startswith("card:") else ""


class _At:
    """The engine's own time for a verb called from inside a step (``ctx.clock_now``)."""

    def __init__(self, now: float) -> None:
        self._now = now

    def now(self) -> float:
        return self._now


def _note(dropped, missing) -> str:
    """One line for the person who owns the card, when a blocker ended without being done."""
    parts = [f"«{d.get('title') or d.get('id')}» ended {d.get('state') or 'dropped'}" for d in dropped]
    parts += [f"{m} is not a card" for m in missing]
    return ("Went on although " + "; ".join(parts) + ": check this card still makes sense.")[:500]


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
    def card_blockers_done(ctx):
        """Sleep while the card this reaction is about waits on cards that are not finished; go on
        when every card in its ``Chain.blocked_by`` is done.

        The card is ``refs.card`` or ``refs.ref`` = ``card:<id>``; a fact that names no card has
        nothing to wait on and goes straight on. The CRM answers which blockers are still open
        (``/internal/cards/blockers``, which reads ``blocked_by`` only). While any is open the step
        keeps their ids in scratch (``open_blockers``) and Waits: ``card_dependents_wake`` wakes it
        the moment one of them changes, and it re-asks on its own after ``blockers_recheck_s``. A
        blocker that ended on a side state (dropped, declined, expired), or an id with no card,
        does not hold it forever: the step goes on and its result carries a ``note`` for the owner."""
        card = card_of(ctx.refs)
        if not card:
            return Done({'card': None, 'waited_on': []})
        base = flows_config.get(CRM_URL_ENV).rstrip('/')
        if not base:
            return NotPresent('crm')
        from flows_steps.common import door_headers, http
        code, answer = http('POST', f'{base}/internal/cards/blockers', door_headers(), {'card': card}, timeout=20)
        if code == 404:
            raise StepError(f'the CRM has no card {card}', retryable=False)
        if code != 200 or not isinstance(answer, dict):
            raise StepError(f'the CRM answered {code} to a blockers read', retryable=code >= 500 or code == 0)
        open_ids = [str(b['id']) for b in answer.get('open') or [] if isinstance(b, dict) and b.get('id')]
        ctx.scratch['card'] = card
        ctx.scratch['open_blockers'] = open_ids
        if open_ids:
            recheck = ctx.flow.param('blockers_recheck_s', BLOCKERS_RECHECK_S) if ctx.flow is not None \
                else BLOCKERS_RECHECK_S
            return Wait(seconds=float(recheck))
        dropped = [{k: d.get(k) for k in ('id', 'state', 'title')}
                   for d in answer.get('dropped') or [] if isinstance(d, dict)]
        missing = [str(m) for m in answer.get('missing') or []]
        result = {'card': card, 'waited_on': list(answer.get('blocked_by') or []),
                  'done': list(answer.get('done') or []), 'dropped': dropped, 'missing': missing}
        if dropped or missing:
            result['note'] = _note(dropped, missing)
        return Done(result)

    @reg.step
    def wake_card_dependents(ctx):
        """A CRM record changed: wake (Flows' own ``wake`` verb) every reaction sleeping in
        ``card_blockers_done`` whose card is that record or waits on it, so it re-asks the CRM now.
        A wake only moves the schedule; whether the reaction goes on is still the CRM's answer."""
        record = str(ctx.refs.get('record_id') or '').strip()
        if not record:
            raise StepError('crm.record.changed carries record_id', retryable=False)
        rows = db.execute(
            "SELECT reaction_id, scratch FROM reaction WHERE step = :step AND status = 'retrying' "
            "AND scratch LIKE :quoted",
            {'step': BLOCKERS_STEP, 'quoted': '%' + json.dumps(record) + '%'})
        woken = []
        for rid, raw in rows:
            try:
                scratch = json.loads(raw or '{}')
            except ValueError:
                continue
            if record != scratch.get('card') and record not in (scratch.get('open_blockers') or []):
                continue
            if wake(db, rid, actor=f'card_dependents_wake:{ctx.reaction_id}', clock=_At(ctx.clock_now),
                    reason=f'card {record} changed (revision {ctx.refs.get("revision")})'):
                woken.append(rid)
        return Done({'record_id': record, 'woken': woken})

    reg.flow(name='crm_change_log', version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['record_crm_change']])
    reg.flow(name='crm_references', version=1, on=EventType('workspace.references.changed'),
             steps=[reg.steps['hand_references_to_crm']])
    reg.flow(name='card_dependents_wake', version=1, on=EventType('crm.record.changed'),
             steps=[reg.steps['wake_card_dependents']])
