"""Hidden admin panel — the internal-tier introspection endpoint + its shaping helpers.

Proves: /api/admin/overview is FAIL-CLOSED (403 with no/wrong secret, and 403 when the deploy
never configured a secret); with the secret it returns classified workloads and typed partial
failures; the pipeline snapshot keys rows correctly and flags native-keyed (S2) carriers.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane import admin_panel
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

from .test_api import _FakeIdentity, _FakeRuntime


def _client(secret: str = "", monkeypatch=None, workloads=None) -> TestClient:
    if monkeypatch is not None:
        monkeypatch.setattr(
            admin_panel, "fetch_workloads",
            lambda url, **kw: [admin_panel.classify_workload(s) for s in (workloads or [])],
        )
    return TestClient(create_app(
        Dispatcher(load_settings(internal_api_secret=secret), _FakeRuntime(), _FakeIdentity()),
    ))


# ── the gate (fail-closed) ────────────────────────────────────────────────────────────────────

def test_admin_overview_403_without_secret():
    assert _client("s3cret").get("/api/admin/overview").status_code == 403


def test_admin_overview_403_with_wrong_secret():
    r = _client("s3cret").get("/api/admin/overview", headers={"X-Internal-Secret": "nope"})
    assert r.status_code == 403


def test_admin_overview_403_when_secret_unconfigured():
    # No configured secret must NOT mean open access — even an empty header is rejected.
    r = _client("").get("/api/admin/overview", headers={"X-Internal-Secret": ""})
    assert r.status_code == 403


def test_admin_overview_with_secret_returns_classified_workloads(monkeypatch):
    workloads = [
        {"workloadId": "mtg-42-abc12345", "state": "running"},
        {"workloadId": "agent-meet-42", "state": "running"},
    ]
    c = _client("s3cret", monkeypatch, workloads)
    r = c.get("/api/admin/overview", headers={"X-Internal-Secret": "s3cret"})
    assert r.status_code == 200
    body = r.json()
    kinds = {w["workloadId"]: w["kind"] for w in body["workloads"]}
    assert kinds == {"mtg-42-abc12345": "bot", "agent-meet-42": "agent-worker"}
    # no redis_url wired in this L2 app → the meetings section degrades with a typed error
    assert body.get("meetings_error")


def test_admin_overview_types_workload_fetch_failure(monkeypatch):
    def _boom(url, **kw):
        raise OSError("kernel unreachable")
    monkeypatch.setattr(admin_panel, "fetch_workloads", _boom)
    c = TestClient(create_app(
        Dispatcher(load_settings(internal_api_secret="s3cret"), _FakeRuntime(), _FakeIdentity()),
    ))
    r = c.get("/api/admin/overview", headers={"X-Internal-Secret": "s3cret"})
    assert r.status_code == 200
    assert "kernel unreachable" in r.json()["workloads_error"]


# ── shaping helpers ───────────────────────────────────────────────────────────────────────────

def test_classify_workload_parses_bot_meeting_id():
    out = admin_panel.classify_workload({"workloadId": "mtg-137-deadbeef"})
    assert out["kind"] == "bot" and out["meeting_id"] == "137"


def test_classify_workload_other():
    assert admin_panel.classify_workload({"workloadId": "traefik"})["kind"] == "other"


class _FakeRedis:
    """Just enough of redis for pipeline_snapshot + the probe's carrier round-trip: streams as
    {key: [(id, fields)]}, plain KV, a set."""

    def __init__(self, streams=None, kv=None, active=None, pending=None):
        self._streams = streams or {}
        self._kv = kv or {}
        self._active = active or set()
        self._pending = pending or {}

    def zrange(self, key, start, stop, withscores=False):
        items = sorted(self._pending.items(), key=lambda kv: kv[1])
        return items if withscores else [k for k, _ in items]

    def set(self, key, value, ex=None):
        self._kv[key] = value

    def delete(self, key):
        self._kv.pop(key, None)
        self._streams.pop(key, None)

    def xadd(self, key, fields):
        self._streams.setdefault(key, []).append(("1-0", fields))

    def scan_iter(self, match="*", count=100):
        import fnmatch
        for k in list(self._streams) + list(self._kv):
            if fnmatch.fnmatch(k, match):
                yield k

    def smembers(self, key):
        return set(self._active)

    def get(self, key):
        return self._kv.get(key)

    def xlen(self, key):
        if key not in self._streams:
            raise KeyError(key)
        return len(self._streams[key])

    def xrevrange(self, key, count=1):
        return list(reversed(self._streams.get(key, [])))[:count]


def test_pipeline_snapshot_rows_and_s2_flag():
    r = _FakeRedis(
        streams={
            "proc:meeting:42": [("1-0", {"note": "{}"}), ("2-0", {"note": "{}"})],
            "tc:meeting:42": [("9-0", {"segment": "{}"})],
            # a native-keyed proc stream — the S2 bug the panel must surface
            "proc:meeting:abc-defg-hij": [("1-0", {"note": "{}"})],
        },
        kv={"proc:meeting:42:on": "1", "proc:meeting:42:cursor": "9-0"},
        active={"42"},
    )
    rows = {row["meeting_id"]: row for row in admin_panel.pipeline_snapshot(r)}
    assert set(rows) == {"42", "abc-defg-hij"}
    assert rows["42"]["row_keyed"] and rows["42"]["in_active_meetings"]
    assert rows["42"]["processing_on"] and rows["42"]["copilot_cursor"] == "9-0"
    assert rows["42"]["proc_stream"] == {"len": 2, "last_id": "2-0"}
    assert rows["42"]["transcript_stream"] == {"len": 1, "last_id": "9-0"}
    assert rows["abc-defg-hij"]["row_keyed"] is False  # never drained by the numeric-keyed db-writer


def test_pipeline_snapshot_includes_live_registry():
    rows = admin_panel.pipeline_snapshot(
        _FakeRedis(),
        [{"numeric_meeting_id": "7", "native_id": "xyz", "platform": "google_meet", "status": "live"}],
    )
    by_id = {row["meeting_id"]: row for row in rows}
    assert "7" in by_id and by_id["7"]["live"]["native_id"] == "xyz"
    # the native ALIAS of the numeric row has no carriers of its own — registry echo, not an S2
    # native-keyed stream; it must NOT appear as a separate (danger-chip) row
    assert "xyz" not in by_id


def test_pipeline_snapshot_keeps_real_native_carriers_despite_alias():
    """A native id that HAS content (a real mis-keyed stream) still shows even when the live
    registry also aliases it to a numeric row — real S2 evidence is never suppressed."""
    r = _FakeRedis(streams={"proc:meeting:xyz": [("1-0", {"note": "{}"})]})
    rows = admin_panel.pipeline_snapshot(
        r, [{"numeric_meeting_id": "7", "native_id": "xyz", "status": "live"}])
    by_id = {row["meeting_id"]: row for row in rows}
    assert "xyz" in by_id and by_id["xyz"]["row_keyed"] is False


def test_pipeline_snapshot_surfaces_pending_drain_and_view_end():
    import time as _time
    now = _time.time()
    r = _FakeRedis(
        streams={"proc:meeting:46": [("1-0", {"note": "{}"}), ("2-0", {"type": "view_end", "cursor": "1-0"})]},
        pending={"46": now + 60, "44": now - 60},  # 46 draining (within deadline), 44 overdue = S1 live
    )
    rows = {row["meeting_id"]: row for row in admin_panel.pipeline_snapshot(r)}
    assert set(rows) == {"46", "44"}  # 44 discovered via the zset alone (no streams left)
    assert rows["46"]["pending_drain"]["overdue"] is False
    assert rows["44"]["pending_drain"]["overdue"] is True
    assert rows["46"]["proc_stream"]["last_type"] == "view_end"


# ── the golden smoke probe ────────────────────────────────────────────────────────────────────

def _ok_http(url, **kw):
    return 5, {"status": "ok", "capabilities": {"bot_spawn": {"status": "ok"}}}


def _probe(r=None, live=None, relay=None, http=_ok_http):
    import time as _time
    relay = relay if relay is not None else {
        "native_resolve": {"ok": True},
        "ingest": {"ok": True, "last_segment_at": _time.time() - 10, "segments": 3},
    }
    return admin_panel.run_probe(load_settings(), r if r is not None else _FakeRedis(),
                                 live or [], relay_health=relay, http_health=http)


def test_probe_all_pass():
    result = _probe()
    assert result["status"] == "pass"
    assert [s["id"] for s in result["stages"]] == ["gateway", "meeting-api", "runtime", "redis", "relay"]
    assert all(s["status"] == "pass" for s in result["stages"])


def test_probe_quiet_relay_is_warn_when_nothing_live_but_fail_when_live():
    stale_relay = {"native_resolve": {"ok": True}, "ingest": {"ok": True, "last_segment_at": None}}
    idle = _probe(relay=stale_relay)
    assert idle["status"] == "warn"
    assert next(s for s in idle["stages"] if s["id"] == "relay")["status"] == "warn"

    busy = _probe(relay=stale_relay, live=[{"status": "live"}])
    assert busy["status"] == "fail"
    assert "LIVE" in next(s for s in busy["stages"] if s["id"] == "relay")["detail"]


def test_probe_stale_live_flag_downgrades_fail_to_warn():
    """A registry-live entry with NO running bot (seen live: meeting 53) is a stale flag —
    relay quiet must be a WARN naming the staleness, not a false FAIL."""
    stale_relay = {"native_resolve": {"ok": True}, "ingest": {"ok": True, "last_segment_at": None}}
    live = [{"status": "live"}]
    no_bots = [{"workloadId": "agent-28-chat-x", "kind": "agent-worker", "state": "running"}]
    result = _probe(relay=stale_relay, live=live)
    assert result["status"] == "fail"  # workloads unknown → trust the registry (unchanged)

    result = admin_panel.run_probe(load_settings(), _FakeRedis(), live,
                                   relay_health=stale_relay, http_health=_ok_http, workloads=no_bots)
    stage = next(s for s in result["stages"] if s["id"] == "relay")
    assert result["status"] == "warn" and stage["status"] == "warn"
    assert "stale live flag" in stage["detail"]

    with_bot = no_bots + [{"workloadId": "mtg-53-abcd1234", "kind": "bot", "state": "running"}]
    result = admin_panel.run_probe(load_settings(), _FakeRedis(), live,
                                   relay_health=stale_relay, http_health=_ok_http, workloads=with_bot)
    assert result["status"] == "fail"  # a real live bot with no segments IS a fault


def test_probe_native_resolve_fault_fails_relay_stage():
    relay = {"native_resolve": {"ok": False, "kind": "unauthorized", "detail": "stale bot key"},
             "ingest": {"ok": True, "last_segment_at": None}}
    result = _probe(relay=relay)
    stage = next(s for s in result["stages"] if s["id"] == "relay")
    assert stage["status"] == "fail" and "stale bot key" in stage["detail"]


def test_probe_service_down_is_typed_stage_failure():
    def _flaky(url, **kw):
        if "gateway" in url:
            raise OSError("connection refused")
        return _ok_http(url)
    result = _probe(http=_flaky)
    assert result["status"] == "fail"
    gw = next(s for s in result["stages"] if s["id"] == "gateway")
    assert gw["status"] == "fail" and "connection refused" in gw["detail"]


def test_probe_endpoint_gate_and_shape(monkeypatch):
    c = TestClient(create_app(
        Dispatcher(load_settings(internal_api_secret="s3cret"), _FakeRuntime(), _FakeIdentity()),
    ))
    assert c.post("/api/admin/probe").status_code == 403

    monkeypatch.setattr(admin_panel, "run_probe",
                        lambda *a, **kw: {"status": "pass", "stages": [], "duration_ms": 1, "at": 0})
    r = c.post("/api/admin/probe", headers={"X-Internal-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["status"] == "pass"
