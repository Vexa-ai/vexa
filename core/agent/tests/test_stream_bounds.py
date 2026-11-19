"""Unit streams stay transport-sized (app.dev 2026-10-16: redis at 10.7 GB, one chat's in/out
streams ~900 MB each, because every turn carried the whole session transcript inline — ~22 MB per
entry — and nothing ever trimmed a unit stream; a cold worker then read its whole in-topic with
``XRANGE - +`` and spawns timed out)."""
from __future__ import annotations

import json

from shared import stream_bounds as sb


class FakeRedis:
    def __init__(self):
        self.kv, self.ttl, self.streams, self.calls = {}, {}, {}, []

    def set(self, k, v, ex=None):
        self.kv[k] = v
        if ex:
            self.ttl[k] = ex
        return True

    def get(self, k):
        return self.kv.get(k)

    def expire(self, k, s):
        self.ttl[k] = s

    def xadd(self, name, fields, maxlen=None, approximate=True):
        self.calls.append(("xadd", name, maxlen, approximate))
        s = self.streams.setdefault(name, [])
        eid = f"{len(s) + 1}-0"
        s.append((eid, dict(fields)))
        if maxlen is not None:
            del s[:-maxlen]
        return eid

    def xrange(self, name, min="-", max="+", count=None):
        self.calls.append(("xrange", name, min, max, count))
        rows = self.streams.get(name, [])

        def key(e):
            a, _, b = e.partition("-")
            return (int(a), int(b or 0))

        lo = min.lstrip("(")
        out = [r for r in rows if min == "-" or (key(r[0]) > key(lo) if min.startswith("(") else key(r[0]) >= key(lo))]
        return out[:count] if count else out


BIG = {"id": "s-1", "version": 3, "files": {"a.jsonl": "x" * 3_000_000}}


def test_in_bundle_goes_by_reference_not_inline():
    r = FakeRedis()
    entry = {"type": "message", "prompt": "hi", "nonce": "n1"}
    sb.attach_in_bundle(r, "u1", entry, BIG)
    assert "session_bundle" not in entry
    ref = entry["session_bundle_ref"]
    assert ref.startswith("unit:u1:bundle:") and r.ttl[ref] == sb.BUNDLE_TTL_S
    assert len(json.dumps(entry)) < 1000
    msg = dict(entry)
    sb.resolve_in_bundle(r.get, msg)
    assert msg["session_bundle"] == BIG


def test_resolve_leaves_inline_bundle_alone_and_tolerates_expired_ref():
    msg = {"session_bundle": {"id": "s"}}
    sb.resolve_in_bundle(lambda k: None, msg)
    assert msg["session_bundle"] == {"id": "s"}
    gone = {"session_bundle_ref": "unit:u:bundle:x"}
    sb.resolve_in_bundle(lambda k: None, gone)
    assert gone.get("session_bundle") is None


def test_handback_goes_by_reference_and_resolves():
    r = FakeRedis()
    ev = {"type": "session-bundle", "session_id": "s-1", "base": 3, "files": {"a": "Zm9v" * 500_000}}
    small = sb.handback_by_ref(r, "u1", ev, tag="t0")
    assert "files" not in small and small["ref"].startswith("unit:u1:handback:")
    assert len(json.dumps(small)) < 1000
    assert sb.resolve_handback(r.get, small)["files"] == ev["files"]


def test_xadd_is_capped_and_streams_expire():
    r = FakeRedis()
    for i in range(50):
        sb.xadd_capped(r, "unit:u:in", {"turn": str(i)}, maxlen=10)
    assert len(r.streams["unit:u:in"]) == 10
    assert r.ttl["unit:u:in"] == sb.UNIT_STREAM_TTL_S
    assert all(c[2] == 10 and c[3] is True for c in r.calls if c[0] == "xadd")


def test_bounded_stream_caps_every_worker_xadd():
    r = FakeRedis()
    s = sb.BoundedStream(r, maxlen=7)
    for i in range(30):
        s.xadd("unit:u:out", {"event": "{}"})
    assert len(r.streams["unit:u:out"]) == 7
    assert s.get("nope") is None          # everything else passes through


def test_boot_read_starts_after_the_cursor_and_is_counted():
    r = FakeRedis()
    for i in range(20):
        r.xadd("unit:u:in", {"turn": str(i)})
    rows = sb.boot_window(r.xrange, "unit:u:in", prior="15-0")
    assert [e for e, _ in rows] == ["16-0", "17-0", "18-0", "19-0", "20-0"]
    last = [c for c in r.calls if c[0] == "xrange"][-1]
    assert last[2] == "(15-0" and last[4] == sb.BOOT_SCAN_MAX
    sb.boot_window(r.xrange, "unit:u:in", prior="")
    assert [c for c in r.calls if c[0] == "xrange"][-1][4] == sb.BOOT_SCAN_MAX
