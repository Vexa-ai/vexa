"""features.py — whether this install serves the CRM at all (``VEXA_FEATURES``).

The CRM is built but not yet proven, so a release install leaves it OFF: crm-api then refuses every
route but its liveness with one typed body, ``feature_disabled``, and its MCP manifest lists no tool.
Rows already written stay where they are and are served again when it is switched on.

The registry and the parse rules are agent-api's, ``core/agent/control_plane/features.py``
(``install_defaults``); a domain may not import another, so this is a copy of that parse for the one
name this service answers, held to it by ``tests/test_features.py``:

    empty or unset  every feature off (the release default)
    ``all``         every feature on (a dev stack)
    ``crm,…``       just the named ones on
    ``all,-crm``    a ``-name`` after ``all`` takes one back

Read from the process environment on every call, so a test (or an operator restart) sees the value
that is set now. ``crm`` is install-scope: no company-layer policy can switch it.

``desktop`` (the desk: placements, arrangement versions, desk proposals) is a second install-scope
name answered by the same parse. It needs ``crm``: the ``/desk/*`` routes refuse with
``feature_disabled`` for ``desktop`` unless BOTH are on, and the served manifest drops the
``desktop_*`` tools when ``desktop`` is off.

``desktop_cards`` (desktop D2: card records, their lifecycle, members, boards and faces) is a third,
needing both: the ``/card/*`` routes refuse with ``feature_disabled`` for ``desktop_cards`` unless all
three are on, the served manifest drops the ``card_*`` tools, and the desk keeps D1's shape.
"""
from __future__ import annotations

import os
import re
from typing import Mapping, Optional

ENV = 'VEXA_FEATURES'
FEATURE = 'crm'
REASON = 'feature_disabled'
SUMMARY = 'the CRM: the record page, crm_* tools, crm-api'
DESKTOP = 'desktop'
DESKTOP_SUMMARY = 'the desktop: workspaces, chosen agents and files, arranged by you or your agent'
CARDS = 'desktop_cards'
CARDS_SUMMARY = 'cards on the desktop: the CRM cards you are on, and the ones agents propose'
#: The routes and tools that ride ``desktop_cards`` (which needs ``desktop`` and ``crm``).
CARD_PREFIX = '/card/'
CARD_TOOL_PREFIX = 'card_'
#: The routes and tools that ride ``desktop``.
DESK_PREFIX = '/desk/'
DESK_TOOL_PREFIX = 'desktop_'

#: What stays served when the CRM is off: liveness, the (then empty) tool manifest, and the OpenAPI
#: the MCP edge reads beside a manifest — none of them is the feature.
ALWAYS_SERVED = frozenset({'/health', '/.well-known/mcp-tools.json', '/openapi.json'})
#: Erasing an individual's personal CRM on account delete (internal secret): served even when the
#: CRM is switched off, because rows written while it was on must still go with the account.
ERASE_PATH = re.compile(r'^/internal/orgs/p-[0-9]+/erase$')


def switched_on(name: str = FEATURE, env: Optional[Mapping[str, str]] = None) -> bool:
    """``name``'s answer from ``VEXA_FEATURES``, by agent-api's ``install_defaults`` rules."""
    raw = str((env if env is not None else os.environ).get(ENV) or '')
    on = False
    for token in (t.strip().lower() for t in raw.split(',')):
        if not token:
            continue
        value = not token.startswith('-')
        token = token.lstrip('-+').strip()
        if token in ('all', name):
            on = value
    return on


#: An individual account's PERSONAL CRM (architecture A1, "individuals have their own CRM"): the CRM
#: serves a person whose home org is their org of one from that org's rows, never the install org's.
#: EXPLICIT: on only when named, never by ``all``, so a dev stack running ``all`` keeps one CRM.
PERSONAL_CRM = 'personal_crm'


def personal_crm_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """``personal_crm`` is on only when named (``…,personal_crm``); ``all`` does not switch it on,
    and ``-personal_crm`` takes it back."""
    raw = str((env if env is not None else os.environ).get(ENV) or '')
    on = False
    for token in (t.strip().lower() for t in raw.split(',')):
        value = not token.startswith('-')
        if token.lstrip('-+').strip() == PERSONAL_CRM:
            on = value
    return on and switched_on(FEATURE, env)


def refusal(name: str = FEATURE, summary: str = SUMMARY) -> dict:
    """The one body every switched-off route answers with (agent-api's ``features.refusal``)."""
    return {
        'status': 'refused',
        'reason': REASON,
        'feature': name,
        'instruction': f'{name} is switched off on this deployment. Do not retry it another way; say '
                       f'it is not available here.',
        'tell_your_person': f"This isn't switched on here yet ({summary}). An administrator can turn it on.",
    }


def desk_served(env: Optional[Mapping[str, str]] = None) -> bool:
    """The desk is served only when both ``crm`` and ``desktop`` are on."""
    return switched_on(FEATURE, env) and switched_on(DESKTOP, env)


def cards_served(env: Optional[Mapping[str, str]] = None) -> bool:
    """Cards (desktop D2) are served only when ``crm``, ``desktop`` and ``desktop_cards`` are all on."""
    return desk_served(env) and switched_on(CARDS, env)
