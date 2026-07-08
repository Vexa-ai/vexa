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
    """Just enough of redis for pipeline_snapshot: streams as {key: [(id, fields)]}, plain KV, a set."""

    def __init__(self, streams=None, kv=None, active=None):
        self._streams = streams or {}
        self._kv = kv or {}
        self._active = active or set()

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
