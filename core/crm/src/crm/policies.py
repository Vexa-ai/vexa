"""policies.py — the desk's limits as the company holds them in `_global/POLICIES.md`.

Four keys (desktop ADR §2.7), each a whole number whose default is the limit this service shipped
with: ``desk_placements_max`` (50 placed items), ``card_proposals_pending_max`` (5 waiting per
person), ``card_proposals_per_agent_per_day`` (20 per agent session per day) and
``card_proposal_ttl_days`` (7). **A company may only narrow them.** A value above the default, one
that is not a whole number, or a ttl below one day is the default, and is named in a log line.

WHERE THE FILE COMES FROM. ``_global`` is the agent domain's tier; a domain may not import another,
so crm-api asks agent-api's own door for it, ``GET /api/workspace/file?path=POLICIES.md&slug=_global``
with the internal tier's secret (``_global`` is readable by every subject; the subject named is
``crm`` so its log line says who asked). The door is ``CRM_AGENT_API_URL``; unset means the agent
domain is not deployed here. **Fail soft, always**: no door, an unreachable one, a refusal, or a file
that is not there resolves to the defaults and changes nothing. The answer, good or not, is kept for
30 seconds, so a burst of desk calls asks once and an admin's change lands within half a minute.

The front-matter parse is a copy of flows' ``flows_steps.policies.front_matter`` (the registry of
every key is flows' ``DEFAULTS``, held to the seed ``behavior/global/POLICIES.md``);
``tests/test_policies.py`` runs both on the same inputs.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

DOOR_ENV = 'CRM_AGENT_API_URL'
POLICIES_FILE = 'POLICIES.md'
GLOBAL_SLUG = '_global'
READER = 'crm'
TTL_S = 30.0
TIMEOUT_S = 3.0

#: The limits, with the values this service enforced before they were policy.
DEFAULTS: dict = {
    'desk_placements_max': 50,
    'card_proposals_pending_max': 5,
    'card_proposals_per_agent_per_day': 20,
    'card_proposal_ttl_days': 7,
}
#: The smallest value each key may take; ``0`` on a proposal count switches proposals off.
MINIMUM = {'card_proposal_ttl_days': 1}

_FENCE = '---'


def front_matter(raw: str) -> dict:
    """The ``key: value`` lines between the opening and closing fence (flows' parse, copied)."""
    text = (raw or "").strip()
    if not text.startswith(_FENCE):
        return {}
    lines = text.splitlines()
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if close is None:
        return {}
    attrs: dict[str, str] = {}
    for line in lines[1:close]:
        if line.strip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip():
            attrs[key.strip().lower()] = value.strip().strip("\"'")
    return attrs


def resolve(raw: Optional[str]) -> tuple[dict, list]:
    """``(limits, problems)``: every key in ``DEFAULTS``, narrowed where the file narrows it."""
    attrs = front_matter(raw or '')
    limits, problems = dict(DEFAULTS), []
    for key, default in DEFAULTS.items():
        value = str(attrs.get(key, '')).strip().replace(',', '')
        if not value:
            continue
        low = MINIMUM.get(key, 0)
        if not value.isdigit():
            problems.append(f'{key}: {value!r} is not a whole number — using the default {default}')
        elif int(value) > default:
            problems.append(f'{key}: {value} is above the default {default}, and a company may only '
                            f'narrow it — using {default}')
        elif int(value) < low:
            problems.append(f'{key}: {value} is below {low} — using the default {default}')
        else:
            limits[key] = int(value)
    return limits, problems


class Policies:
    """The limits as ``_global`` holds them now, read through agent-api's door, cached ``ttl`` seconds."""

    def __init__(self, agent_url: str = '', secret: str = '', *, ttl: float = TTL_S, transport=None,
                 now: Callable[[], float] = time.monotonic):
        self.base = str(agent_url or '').strip().rstrip('/')
        self.secret = str(secret or '')
        self.ttl, self.transport, self.now = ttl, transport, now
        self._at: Optional[float] = None
        self._limits = dict(DEFAULTS)
        self._lock = threading.Lock()

    def fetch(self) -> Optional[str]:
        """``POLICIES.md`` from ``_global``, or None for anything but a 200 with content."""
        if not self.base:
            return None
        try:
            with httpx.Client(transport=self.transport, timeout=TIMEOUT_S) as client:
                r = client.get(self.base + '/api/workspace/file',
                               params={'path': POLICIES_FILE, 'slug': GLOBAL_SLUG},
                               headers={'X-User-Id': READER, 'X-Internal-Secret': self.secret})
            body = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.info('policies: could not read %s/%s (%s) — using the default desk limits',
                        GLOBAL_SLUG, POLICIES_FILE, type(exc).__name__)
            return None
        content = body.get('content') if isinstance(body, dict) else None
        return content if isinstance(content, str) else None

    def limits(self) -> dict:
        with self._lock:
            now = self.now()
            if self._at is None or now - self._at >= self.ttl:
                limits, problems = resolve(self.fetch())
                for problem in problems:
                    logger.warning('policies: %s', problem)
                self._limits, self._at = limits, now
            return dict(self._limits)

    __call__ = limits
