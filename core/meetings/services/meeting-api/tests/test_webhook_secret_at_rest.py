"""MIGRATION-0015: a webhook signing secret is stored sealed, and every subscriber keeps verifying.

The secret is presented onward (it signs every delivery), so it is sealed with AES-256-GCM under a
key mounted apart from the database (`VEXA_AT_REST_KEY_FILE`), never hashed. These pin:

  * a value sealed in admin-api opens here (one vendored module, one key file);
  * a legacy plaintext value — a meeting row or a queued retry from before the upgrade — still
    signs exactly as before, and so does the same secret once sealed: the subscriber's verifier
    accepts both deliveries;
  * the retry queue (Redis) holds the sealed form, never the secret;
  * a wrong key, another purpose or a tampered value opens to nothing — never to a wrong secret.
"""
import asyncio
import json
import secrets
from pathlib import Path

import pytest

from meeting_api import at_rest
from meeting_api.webhooks import retry as retry_mod
from meeting_api.webhooks.delivery import build_headers, verify_signature

SUBSCRIBER_SECRET = "whsec_customer_configured_this_in_2025"
PURPOSE = at_rest.WEBHOOK_SECRET_PURPOSE


@pytest.fixture()
def key(tmp_path, monkeypatch):
    f = tmp_path / "at-rest.key"
    f.write_text(secrets.token_hex(32) + "\n")
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(f))
    return f


def test_the_admin_api_copy_is_the_same_module():
    here = Path(at_rest.__file__).read_text()
    root = Path(__file__).resolve().parents[4]
    there = (root / "identity/services/admin-api/src/admin_api/at_rest.py").read_text()
    assert here == there


def test_sealed_value_carries_no_secret_and_opens_to_it(key):
    sealed = at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)
    assert sealed.startswith("vxe1:") and SUBSCRIBER_SECRET not in sealed
    assert at_rest.unseal(sealed, PURPOSE) == SUBSCRIBER_SECRET
    assert at_rest.seal(sealed, PURPOSE) == sealed                 # never sealed twice


def test_existing_webhooks_keep_verifying_after_the_migration(key):
    """The subscriber holds the secret they set; a delivery signed from the pre-upgrade plaintext
    row and one signed from the sealed row both verify with it."""
    payload = json.dumps({"event": "meeting.completed", "meeting": {"id": 1}}).encode()
    legacy_row = {"webhook_url": "https://hooks.example/x", "webhook_secret": SUBSCRIBER_SECRET}
    migrated_row = {**legacy_row, "webhook_secret": at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)}
    for row in (legacy_row, migrated_row):
        headers = build_headers(at_rest.unseal(row["webhook_secret"], PURPOSE), payload, timestamp="1700000000")
        assert verify_signature(payload, headers, SUBSCRIBER_SECRET)
        assert headers["Authorization"] == f"Bearer {SUBSCRIBER_SECRET}"


def test_wrong_key_purpose_or_tamper_never_opens_to_a_secret(key, tmp_path, monkeypatch):
    sealed = at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)
    with pytest.raises(at_rest.SealedSecretUnreadable):
        at_rest.unseal(sealed, "another_purpose")
    with pytest.raises(at_rest.SealedSecretUnreadable):
        at_rest.unseal(sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1], PURPOSE)
    other = tmp_path / "other.key"
    other.write_text(secrets.token_hex(32))
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(other))
    with pytest.raises(at_rest.SealedSecretUnreadable):
        at_rest.unseal(sealed, PURPOSE)
    monkeypatch.delenv(at_rest.KEY_FILE_ENV)
    with pytest.raises(at_rest.SealedSecretUnreadable):           # no key: unreadable, never plaintext
        at_rest.unseal(sealed, PURPOSE)


def test_without_a_key_nothing_is_stored_unsealed(monkeypatch):
    monkeypatch.delenv(at_rest.KEY_FILE_ENV, raising=False)
    assert not at_rest.key_configured()
    with pytest.raises(at_rest.AtRestKeyUnavailable):
        at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)


def test_a_malformed_key_file_is_refused_loudly(tmp_path, monkeypatch):
    bad = tmp_path / "k"
    bad.write_text("not-hex")
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(bad))
    with pytest.raises(at_rest.AtRestKeyUnavailable):
        at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)


def test_meeting_api_refuses_to_boot_without_the_key(monkeypatch, tmp_path):
    from meeting_api.__main__ import _at_rest_key_at_boot
    from meeting_api.config_preflight import ConfigError

    monkeypatch.delenv(at_rest.KEY_FILE_ENV, raising=False)
    with pytest.raises(ConfigError, match="refuses to boot"):
        _at_rest_key_at_boot()
    (tmp_path / "k").write_text("too-short")
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(tmp_path / "k"))
    with pytest.raises(ConfigError, match="refuses to boot"):
        _at_rest_key_at_boot()
    (tmp_path / "k").write_text(secrets.token_hex(32))
    _at_rest_key_at_boot()


def test_the_key_file_is_read_once(key, monkeypatch):
    at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or real(self, *a, **k))
    for _ in range(5):
        at_rest.unseal(at_rest.seal(SUBSCRIBER_SECRET, PURPOSE), PURPOSE)
    assert reads == []


class _Redis:
    def __init__(self):
        self.items = []

    async def rpush(self, _key, value):
        self.items.append(value)


def test_the_retry_queue_holds_the_sealed_form_and_still_signs(key):
    redis = _Redis()
    queue = retry_mod.RetryQueue(redis)
    envelope = {"event": "meeting.completed"}
    asyncio.run(queue.enqueue("https://hooks.example/x", envelope, SUBSCRIBER_SECRET))
    stored = redis.items[0]
    assert SUBSCRIBER_SECRET not in stored
    entry = json.loads(stored)
    seen = {}

    async def transport(url, body, headers):
        seen.update(headers=headers, body=body)
        return type("R", (), {"status_code": 200})()

    ok, _code, _err = asyncio.run(retry_mod._deliver_one(entry, transport))
    assert ok and verify_signature(seen["body"], seen["headers"], SUBSCRIBER_SECRET)

    legacy_entry = {**entry, "webhook_secret": SUBSCRIBER_SECRET}  # queued before the upgrade
    asyncio.run(retry_mod._deliver_one(legacy_entry, transport))
    assert verify_signature(seen["body"], seen["headers"], SUBSCRIBER_SECRET)


class _QueueRedis:
    """Enough of Redis for one sweep of the retry queue."""

    def __init__(self, entries):
        self.lists = {retry_mod.RETRY_QUEUE_KEY: [json.dumps(e) for e in entries]}

    def _l(self, k):
        return self.lists.setdefault(k, [])

    async def llen(self, k):
        return len(self._l(k))

    async def rpoplpush(self, src, dst):
        if not self._l(src):
            return None
        v = self._l(src).pop()
        self._l(dst).insert(0, v)
        return v

    async def lset(self, k, i, v):
        self._l(k)[i] = v

    async def lrem(self, k, n, v):
        if v in self._l(k):
            self._l(k).remove(v)

    async def lpush(self, k, v):
        self._l(k).insert(0, v)

    async def rpush(self, k, v):
        self._l(k).append(v)

    async def lrange(self, k, a, b):
        return list(self._l(k))

    async def ltrim(self, k, a, b):
        pass

    async def expire(self, *a):
        pass


def test_a_queued_secret_that_does_not_open_is_dead_lettered_never_sent_unsigned(key, tmp_path, monkeypatch):
    sealed = at_rest.seal(SUBSCRIBER_SECRET, PURPOSE)
    entry = {"url": "https://hooks.example/x", "payload": {"event": "meeting.completed"}, "webhook_secret": sealed,
             "attempt": 0, "next_retry_at": 0, "created_at": 1_000_000_000}
    other = tmp_path / "replaced.key"
    other.write_text(secrets.token_hex(32))
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(other))          # the key file was replaced
    sent = []

    async def transport(url, body, headers):
        sent.append(headers)
        return type("R", (), {"status_code": 200})()

    redis = _QueueRedis([entry])
    asyncio.run(retry_mod.drain_retry_queue(redis, transport, now=1_000_000_100))
    assert sent == []                                             # nothing went out, signed or not
    dead = [json.loads(v) for v in redis.lists.get(retry_mod.DEAD_LETTER_KEY, [])]
    assert dead and dead[0].get("reason") == retry_mod.SECRET_UNREADABLE
