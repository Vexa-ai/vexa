"""card_templates.py — the seeded lifecycle templates of the card kinds (PRD 0.13.4 D11, D12).

A card of a KIND follows that kind's template: its stages, its side states, and the GATES a person
passes it through. A template is SEEDED POLICY, not code (P10/P11, R12): the defaults below are
the policy-line registry's ``card_template.<kind>`` tuning lines, and an org may restate any of them
in ``_global/POLICIES.md`` as ``card_templates.<kind>: Stage > Stage > … | Side | Side`` (the stage
names only; the gate semantics below are the platform's and cannot be edited, because they are who
may move a card — R6, R10).

The three templates the converged model fixes (§13), and the kinds that share them:

* **commitment** — proposed → accepted (the doer acknowledges) → delivered (with evidence) →
  confirmed (by the person waiting) → closed.
* **approval** (also ``invitation``, ``policy_change``) — opened → decided (by the signer) →
  executed (an effect receipt) → closed.
* **question** (also ``ask``) — asked → answered → used (whoever asked acknowledges) → closed.

TAKEN IS NOT PASSED (D12): every handoff is two-phase. A gate names its next actor, who is
``offered`` the card; only that person's acknowledgement (or a gate action they take, which
acknowledges implicitly) moves custody. A gate passes only on its OWNER's confirmation: delivering is
the doer's claim, confirming is the waiting person's. ``execute`` passes only with an effect-receipt
reference. Every gate action is one append-only revision with the decision record (D13).
"""
from __future__ import annotations

#: kind -> template name
KIND_TEMPLATE = {
    'commitment': 'commitment',
    'approval': 'approval', 'invitation': 'approval', 'policy_change': 'approval',
    'question': 'question', 'ask': 'question',
}
KINDS = tuple(KIND_TEMPLATE)

#: Who a gate's next actor becomes after it passes: the same person, the requester (the person
#: waiting), or nobody (the card is finished).
SAME, REQUESTER, NOBODY = 'same', 'requester', 'nobody'

#: A gate: ``from`` the stages it may be taken in, ``to`` the stage it moves to, ``needs`` what the
#: action must carry (``evidence`` refs, a ``receipt`` ref, an ``answer``), ``then`` who is offered
#: the card next. ``acknowledge`` and ``decline`` exist for every template.
TEMPLATES: dict = {
    'commitment': {
        'stages': ['proposed', 'accepted', 'delivered', 'confirmed', 'closed'],
        'side': ['declined', 'expired'],
        'gates': {
            'acknowledge': {'from': ['proposed'], 'to': 'accepted', 'then': SAME},
            'deliver': {'from': ['proposed', 'accepted'], 'to': 'delivered', 'needs': 'evidence', 'then': REQUESTER},
            'confirm': {'from': ['delivered'], 'to': 'closed', 'then': NOBODY, 'passes': 'confirmed'},
            'decline': {'from': ['proposed', 'accepted'], 'to': 'declined', 'then': NOBODY},
        },
    },
    'approval': {
        'stages': ['opened', 'decided', 'executed', 'closed'],
        'side': ['declined', 'expired'],
        'gates': {
            'acknowledge': {'from': ['opened'], 'to': None, 'then': SAME},
            'decide': {'from': ['opened'], 'to': 'decided', 'then': REQUESTER},
            'execute': {'from': ['decided'], 'to': 'closed', 'needs': 'receipt', 'then': NOBODY, 'passes': 'executed'},
            'decline': {'from': ['opened'], 'to': 'declined', 'then': NOBODY},
        },
    },
    'question': {
        'stages': ['asked', 'answered', 'used', 'closed'],
        'side': ['declined', 'expired'],
        'gates': {
            'acknowledge': {'from': ['asked'], 'to': None, 'then': SAME},
            'answer': {'from': ['asked'], 'to': 'answered', 'needs': 'answer', 'then': REQUESTER},
            'use': {'from': ['answered'], 'to': 'closed', 'then': NOBODY, 'passes': 'used'},
            'decline': {'from': ['asked'], 'to': 'declined', 'then': NOBODY},
        },
    },
}

#: The order actions are offered in a decision block (the primary first, decline last — R11).
ACTION_ORDER = ('acknowledge', 'decide', 'answer', 'deliver', 'confirm', 'use', 'execute', 'decline')
#: What a person reads on the button and in History.
ACTION_LABELS = {'acknowledge': 'Accept', 'decide': 'Approve', 'answer': 'Answer', 'deliver': 'Mark delivered',
                 'confirm': 'Confirm', 'use': 'Done', 'execute': 'Executed', 'decline': 'Decline'}
DONE_WORDS = {'acknowledge': 'Accepted', 'decide': 'Approved', 'answer': 'Answered', 'deliver': 'Delivered',
              'confirm': 'Confirmed', 'use': 'Used', 'execute': 'Executed', 'decline': 'Declined'}


def template_for(kind, override_stages=None):
    """The template of a card kind (a copy), or None for a card with no kind (a plain board card).
    ``override_stages`` is the org's ``card_templates.<kind>`` line parsed as ``{stages, side}``; it
    renames stages only when it keeps their count, so a gate always lands on a stage."""
    name = KIND_TEMPLATE.get(str(kind or ''))
    if not name:
        return None
    base = TEMPLATES[name]
    out = {'name': name, 'stages': list(base['stages']), 'side': list(base['side']),
           'gates': {k: dict(v) for k, v in base['gates'].items()}}
    if override_stages and len(override_stages.get('stages') or []) == len(base['stages']) \
            and len(override_stages.get('side') or []) == len(base['side']):
        rename = dict(zip(base['stages'] + base['side'], override_stages['stages'] + override_stages['side']))
        out['stages'] = [rename[s] for s in base['stages']]
        out['side'] = [rename[s] for s in base['side']]
        for g in out['gates'].values():
            g['from'] = [rename[s] for s in g['from']]
            if g.get('to'):
                g['to'] = rename[g['to']]
            if g.get('passes'):
                g['passes'] = rename[g['passes']]
    return out


def is_open(template, state):
    return state not in template['side'] and state != template['stages'][-1]


def gate_strip(template, state, decisions):
    """The route as a strip of gates (converged §13): each stage after the first is a gate, ``done``
    / ``current`` / ``ahead``, with who passed it and when (from the decision records)."""
    stages = template['stages']
    passed_by = {}
    for d in reversed(decisions or []):          # oldest first, so the latest pass wins
        gate = template['gates'].get(d.get('action')) or {}
        for stage in (gate.get('passes'), gate.get('to')):
            if stage:
                passed_by[stage] = {'by': d.get('by'), 'at': d.get('at')}
    if state in template['side']:
        idx = -1
    else:
        idx = stages.index(state) if state in stages else 0
    out = []
    for n, stage in enumerate(stages[1:], start=1):
        if idx < 0:
            st = 'done' if stage in passed_by else 'ahead'
        elif n <= idx:
            st = 'done'
        elif n == idx + 1:
            st = 'current'
        else:
            st = 'ahead'
        who = passed_by.get(stage) or {}
        out.append({'name': stage, 'state': st, 'by': who.get('by') if st == 'done' else None,
                    'at': who.get('at') if st == 'done' else None})
    return out


def actions_for(template, state, handoff, viewer, next_actor, requester):
    """The gate actions THIS viewer may take now. Only the next actor acts on an open card; the
    requester confirms/uses because they become the next actor at that gate (D12: a gate passes only
    on its owner's confirmation). ``acknowledge`` is offered only while the handoff is ``offered``."""
    if not viewer or viewer != next_actor or not is_open(template, state):
        return []
    out = []
    for action in ACTION_ORDER:
        gate = template['gates'].get(action)
        if not gate or state not in gate['from']:
            continue
        if action == 'acknowledge' and handoff == 'acknowledged':
            continue
        out.append(action)
    return out
