"""The person's clock — `current_time` and `timezone_set`, as agent-api routes.

The timezone is identity's fact (`/internal/users/{id}/settings`); the routes read and write it
there over the internal tier. Identity is faked at urlopen.
"""
import io
import json
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from control_plane import route_policy
from control_plane.routers import clock


def test_lisbon_dst_and_winter():
    for month, hour in ((10, 21), (12, 20)):
        r = clock.clock_context('Europe/Lisbon', datetime(2026, month, 7, 20, 20, tzinfo=timezone.utc))
        assert r['local_now'].startswith(f'2026-{month:02}-07T{hour}:20')
        assert not r['timezone_required']


def test_unknown_and_invalid_ask_without_guessing():
    for zone in ('', 'invalid/zone'):
        r = clock.clock_context(zone)
        assert r['timezone_required'] and r['timezone'] is None and 'local_now' not in r


class _Identity:
    def __init__(self, status=200):
        self.status, self.saved, self.requests = status, {}, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, 'x', {}, io.BytesIO(b'{}'))
        if req.get_method() == 'PUT':
            self.saved.update(json.loads(req.data))


class _Resp:
    def __init__(self, body):
        self.status, self._body = 200, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(monkeypatch, status=200):
    ident = _Identity(status)

    def urlopen(req, timeout=None):
        ident(req)
        return _Resp(json.dumps(ident.saved).encode())

    monkeypatch.setattr(clock.urllib.request, 'urlopen', urlopen)
    settings = SimpleNamespace(admin_api_url='http://admin-api:8001',
                               internal_api_secret=SecretStr('internal'))

    app = FastAPI(dependencies=[route_policy.PERSON_GATE])   # agent-api's own person gate
    app.include_router(clock.build(subject_of=lambda r: r.headers['x-user-id'], settings=settings))
    return TestClient(app), ident


def test_the_clock_reads_and_writes_identity_over_the_internal_tier(monkeypatch):
    c, ident = _client(monkeypatch)
    assert c.get('/api/time', headers={'X-User-Id': '7'}).json()['timezone_required']
    r = c.put('/api/time/zone', headers={'X-User-Id': '7'}, json={'timezone': 'Europe/Lisbon'})
    assert r.json()['status'] == 'saved' and r.json()['timezone'] == 'Europe/Lisbon'
    assert c.get('/api/time', headers={'X-User-Id': '7'}).json()['timezone'] == 'Europe/Lisbon'
    req = ident.requests[-1]
    assert req.full_url == 'http://admin-api:8001/internal/users/7/settings'
    assert req.headers['X-internal-secret'] == 'internal'


def test_an_invalid_zone_is_asked_for_again_without_a_write(monkeypatch):
    c, ident = _client(monkeypatch)
    assert c.put('/api/time/zone', headers={'X-User-Id': '7'},
                 json={'timezone': 'Mars/Olympus'}).json()['status'] == 'invalid_timezone'
    assert not ident.requests


def test_an_identity_outage_is_not_reported_as_a_missing_timezone(monkeypatch):
    c, _ = _client(monkeypatch, status=503)
    result = c.get('/api/time', headers={'X-User-Id': '7'}).json()
    assert result['preference_status'] == 'unavailable'
    assert 'Do not claim they are missing' in result['instruction']


def test_a_worker_without_a_person_cannot_change_the_timezone(monkeypatch):
    c, ident = _client(monkeypatch)
    r = c.put('/api/time/zone', headers={'X-User-Id': '7', 'X-User-Regime': 'autonomous'},
              json={'timezone': 'Europe/Lisbon'})
    assert r.status_code == 403 and not ident.requests


# ── the clock rides every turn that holds the toolbelt ───────────────────────────────────────────
# `whats_waiting` once carried a fresh clock as `time_context` on the dogfood rig. On a standard
# deployment `whats_waiting` is flows' read model and the clock is agent-api's `current_time`, so
# the turn is told — every turn whose worker can call it — that the time is a tool, not a guess.

def test_a_turn_with_the_toolbelt_is_told_the_clock_is_current_time():
    from control_plane.api_shared import toolbelt_preamble

    said = toolbelt_preamble("s-123")
    assert "`current_time`" in said and "`timezone_set`" in said
    assert "timezone_required" in said
    # …and the naming ask it rides with is unchanged
    assert 'Current chat session: "s-123"' in said and "call chat_name" in said
