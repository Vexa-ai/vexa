"""profiles.py — a contact LINKS to a person's published public profile (R4, registry ADR § "a CRM
record may link to a public profile; it never copies it").

THE LINK is one relationship row on the record (``kind='links_profile'``, ``target_domain='profile'``,
``target_id=<handle>``), written by the store's own write path, so only someone who may change the
record links it. At most one profile per record.

THE PUBLIC FIELDS ARE READ LIVE from identity (``GET /internal/profiles/{handle}``) on every read and
never stored here: when the person changes a field the contact shows it next time, and when they
withdraw the profile the contact is the org's own data again (identity answers 404 exactly as for a
handle that never existed).

WHAT IDENTITY LEARNS is only ``{org, linked}`` on ``POST /internal/profiles/{handle}/links``, which it
keeps as a salted hash, so the owner sees how MANY orgs link to them — never which, never a note.
Nothing an org wrote (fields, narrative, record ids) is ever sent.

Behind ``public_profiles``, EXPLICIT: on only when ``VEXA_FEATURES`` names it (``all`` does not).
"""
from __future__ import annotations

import os
import re
from typing import Mapping, Optional
from urllib.parse import quote

import httpx

FEATURE = 'public_profiles'
SUMMARY = 'public profiles: contacts linked to a person\'s own published profile'
PREFIX = '/profile/'
KIND = 'links_profile'
DOMAIN = 'profile'
HANDLE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')


def switched_on(env: Optional[Mapping[str, str]] = None) -> bool:
    raw = str((env if env is not None else os.environ).get('VEXA_FEATURES') or '')
    on = False
    for token in (t.strip().lower() for t in raw.split(',')):
        if token.lstrip('-+') == FEATURE:
            on = not token.startswith('-')
    return on


class ProfileUnavailable(Exception):
    pass


class ProfileClient:
    """identity's two internal profile doors, with the internal secret."""

    def __init__(self, base: str, secret: str, transport=None, timeout: float = 5.0):
        self.base, self.secret, self.transport, self.timeout = base.rstrip('/'), secret, transport, timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=self.timeout,
                            headers={'X-Internal-Secret': self.secret})

    def public(self, handle: str) -> Optional[dict]:
        """The published profile, or None (withdrawn, never published, or no such handle)."""
        try:
            with self._client() as c:
                r = c.get(f'{self.base}/internal/profiles/{quote(handle, safe="")}')
        except httpx.HTTPError as e:
            raise ProfileUnavailable(str(e)) from e
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProfileUnavailable(f'identity answered {r.status_code}')
        body = r.json()
        # only the public shape crosses: never anything else identity might add
        return {'handle': body.get('handle'), 'fields': dict(body.get('fields') or {}),
                'company_verified': body.get('company_verified')}

    def tell(self, handle: str, org: str, linked: bool) -> bool:
        """Tell identity this org linked (or unlinked). False when the profile is not published."""
        try:
            with self._client() as c:
                r = c.post(f'{self.base}/internal/profiles/{quote(handle, safe="")}/links',
                           json={'org': org, 'linked': linked})
        except httpx.HTTPError as e:
            raise ProfileUnavailable(str(e)) from e
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            raise ProfileUnavailable(f'identity answered {r.status_code}')
        return True
