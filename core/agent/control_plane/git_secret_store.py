"""git_secret_store.py — agent-api's one way to keep and use a Git credential: the credential broker.

Saved git tokens (``pat/<subject>`` for the built-in GitHub provider, ``pat/<subject>/<provider>`` for
any other configured one) and per-workspace deploy keys (``deploy/<key>.priv|.pub``)
live in the credential broker (ADR-0040), behind the ``git`` role of ``credential-broker.v1``.
agent-api holds the git role's signing key and nothing else: no store, no store key. A value comes
back over the signed wire only to the git role, is used in-process for ONE git operation, and is
never written to a workspace, a response body or a log.

The broker serves the git role only for the person the gateway signed for, so each call is made as
the person the request being served acts for: the subject and the gateway's signature that
`broker_client.ForwardedIdentity` holds. Outside such a request there is nobody to act for, and
nothing is sent.

Fail loud, never fall back:

* no broker configured → a save is :class:`GitStoreUnavailable` naming the gap; a read answers
  "no credential" only when there is none to find, and refuses when an older entry is waiting to
  move (that person HAS a credential, and running git without it would be a silent anonymous run).
  Clearing works without a broker: it removes that older entry, which is the person's way out;
* broker unreachable, refusing or malformed → :class:`GitStoreUnavailable`, never absence.

Entries agent-api kept itself before (``control_plane.legacy_git_store``) move on first use: read
there, written to the broker, read back, and only then removed — and a tombstone the broker already
holds wins over them, so a deleted credential is never resurrected. :func:`report_legacy` counts what
is still waiting at boot (counts only).
"""
from __future__ import annotations

import logging
import os
import re

from control_plane import broker_client
from control_plane import legacy_git_store as legacy

log = logging.getLogger(__name__)

SECRETS_DIRNAME = legacy.SECRETS_DIRNAME
NAME = re.compile(r'^(pat/[A-Za-z0-9_.-]{1,128}(?:/[a-z0-9][a-z0-9.-]{0,63})?|deploy/(?:user|ws)-[A-Za-z0-9_.-]{1,120}\.(?:priv|pub))$')

#: The typed fault an API answer carries, so the chat can say what is wrong instead of "error".
FAULT = "git_credentials_unavailable"
UNCONFIGURED = ("Git credentials are not configured on this deployment: saved tokens and deploy keys are "
                "kept in the credential broker, and agent-api has no VEXA_GIT_STORE_BROKER_URL. A token "
                "typed for one operation still works.")
PENDING = ("A saved Git credential is waiting to move into the credential broker, which this deployment "
           "does not configure (VEXA_GIT_STORE_BROKER_URL). It is not used until it has moved; a saved "
           "token can be cleared to continue without it.")


class GitStoreUnavailable(RuntimeError):
    """The Git credential store cannot answer. Never means "no credential"."""


def enabled() -> bool:
    return bool(os.environ.get('VEXA_GIT_STORE_BROKER_URL'))


def _check(name: str) -> None:
    if not NAME.fullmatch(name or '') or '..' in name:
        raise ValueError('Invalid Git credential name')


def _call(name, action, value=None):
    """One Git credential RPC as the git role, signed by the shared broker client. Every failure —
    unconfigured, unreachable, refused, malformed — is logged by kind there and raised here as
    GitStoreUnavailable, never read as an absent credential."""
    _check(name)
    path = '/api/internal/git-secret'
    subject, identity = broker_client.forwarded()
    try:
        if not identity:
            raise broker_client.fault('identity_missing', role='git', method='POST', path=path)
        r = broker_client.request(
            base_url=os.environ.get('VEXA_GIT_STORE_BROKER_URL', ''),
            key_file=os.environ.get('VEXA_GIT_STORE_KEY_FILE', ''),
            role='git', actor=subject, method='POST', path=path,
            payload={'name': name, 'action': action, 'value': value}, timeout=10, identity=identity)
        if r.status_code != 200:
            raise broker_client.fault('http_%d' % r.status_code, role='git', method='POST', path=path, status=r.status_code)
        result = broker_client.json_of(r, role='git', method='POST', path=path)
        if not isinstance(result, dict) or not isinstance(result.get('found'), bool) or \
                result.get('value') is not None and not isinstance(result['value'], str):
            raise broker_client.fault('parse', role='git', method='POST', path=path, status=r.status_code)
        return result
    except broker_client.BrokerFault as exc:
        if exc.kind == 'identity_missing':
            raise GitStoreUnavailable('Git credentials are used only for a person signed in through the gateway') from None
        raise GitStoreUnavailable('Git credential store unavailable; retry when Connections is healthy') from None


def get(root, name):
    """The credential stored under ``name``, or ``None`` when there is none."""
    _check(name)
    if not enabled():
        if legacy.state(root, name) != legacy.ABSENT:
            raise GitStoreUnavailable(PENDING)
        return None
    remote = _call(name, 'get')
    if remote['found']:
        # The broker's answer is authoritative; an old copy left beside it is removed.
        if legacy.has_entry(root, name):
            legacy.delete(root, name)
        return remote.get('value')
    value = legacy.get(root, name)
    if value is None:
        if legacy.state(root, name) == legacy.UNREADABLE:
            raise GitStoreUnavailable('Existing Git credential cannot be decrypted for migration')
        return None
    remote = _call(name, 'migrate', value)
    if remote['found'] and remote.get('value') == value:
        legacy.delete(root, name)
        log.info("moved one Git credential into the credential broker")
    return remote.get('value')


def put(root, name, value):
    """Store (or, with a falsy ``value``, clear) ``name`` in the broker. True when one is now held."""
    _check(name)
    value = (value or '').strip() or None
    if not enabled():
        if value is None:            # clearing needs no store: drop what an older build left here
            legacy.delete(root, name)
            return False
        raise GitStoreUnavailable(UNCONFIGURED)
    remote = _call(name, 'put', value)
    if not remote['found'] or remote.get('value') != value:
        raise GitStoreUnavailable('Git credential storage confirmation failed')
    legacy.delete(root, name)
    return value is not None


def delete(root, name):
    existed = get(root, name) is not None
    put(root, name, None)
    return existed


def report_legacy(root) -> int:
    """Boot: how many Git credentials agent-api still holds from before the broker kept them. Logs the
    count and what moves them — never a name or a value. Each moves on its owner's next Git operation;
    `credential_broker.import_git_legacy` moves them all at once."""
    count = len(legacy.pending(root))
    if count:
        log.warning("%d Git credential(s) from agent-api's retired local store are waiting to move into the "
                    "credential broker%s. Each moves on its owner's next Git operation; to move them all now, "
                    "run the broker's one-shot import (docs: Connections → Git credentials).",
                    count, "" if enabled() else ", which this deployment does not configure")
    else:
        legacy.retire_key_if_empty(root)
    return count
