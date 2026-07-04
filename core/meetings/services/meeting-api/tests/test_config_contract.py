"""config.v1 (ADR-0026) — meeting-api's declaration, boot preflight, capability tri-state,
/health rows, and the CANONICAL capability gate: the spawn-time STT 503 driven by the declared
`stt` capability instead of ad-hoc os.getenv checks.

All offline: the STT live probe is monkeypatched where a test exercises it (`_run_probe` is the
seam); env-level tri-state tests pass explicit env dicts (pure, no monkeypatching).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api import config_preflight as cp
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

HEADERS = {"x-user-id": "7"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    cp._reset_probe_cache()
    yield
    cp._reset_probe_cache()


def _client(repo=None):
    return TestClient(create_app(meeting_repo=repo or InMemoryMeetingRepo(), runtime=FakeRuntimeClient()))


# ── the declaration itself ───────────────────────────────────────────────────────────────────────


def test_declaration_loads_and_is_internally_consistent():
    decl = cp.load_declaration()
    assert decl["service"] == "meeting-api"
    caps = decl["capabilities"]
    assert set(caps) == {"stt", "object_storage"}
    # the canonical capability carries the live auth probe (the silent-401 incident's fix)
    assert caps["stt"]["probe"]["kind"] == "http"
    # every capability-classed key resolves (load_declaration raises otherwise) and stt's members
    # are exactly the two keys the original ad-hoc guard checked
    stt_keys = {k["key"] for k in decl["keys"] if k.get("capability") == "stt"}
    assert stt_keys == {"TRANSCRIPTION_SERVICE_URL", "TRANSCRIPTION_SERVICE_TOKEN"}
    # required-explicit is exactly the A4 boot bar
    required = {k["key"] for k in decl["keys"] if k["class"] == "required-explicit"}
    assert required == {"ADMIN_TOKEN"}


# ── boot preflight (A4, now declaration-driven) ──────────────────────────────────────────────────


def test_preflight_refuses_to_boot_without_admin_token(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight()
    assert "ADMIN_TOKEN" in str(ei.value), "the boot error must NAME the missing required key"


def test_preflight_reports_capability_rows(monkeypatch):
    # STT env-configured (conftest) + a passing probe → the boot report carries the rows.
    monkeypatch.setattr(cp, "_run_probe", lambda spec, env: {"ok": True, "status": 405})
    report = cp.preflight()
    assert report["service"] == "meeting-api"
    assert report["capabilities"]["stt"]["state"] == cp.CONFIGURED
    assert report["capabilities"]["stt"]["probe"]["ok"] is True
    assert "object_storage" in report["capabilities"]


# ── the capability tri-state (env-level, pure) ───────────────────────────────────────────────────


def test_stt_tri_state():
    both = {"TRANSCRIPTION_SERVICE_URL": "http://stt", "TRANSCRIPTION_SERVICE_TOKEN": "t"}
    assert cp.capability_states(both)["stt"] == cp.CONFIGURED
    assert cp.capability_states({})["stt"] == cp.NOT_CONFIGURED
    # SOME-but-not-all set is its own state — a half-configured deploy must not look unconfigured
    url_only = {"TRANSCRIPTION_SERVICE_URL": "http://stt"}
    assert cp.capability_states(url_only)["stt"] == cp.MISCONFIGURED
    # empty string counts as unset (compose `${VAR:-}` defaults absent vars to "")
    blank = {"TRANSCRIPTION_SERVICE_URL": "http://stt", "TRANSCRIPTION_SERVICE_TOKEN": "  "}
    assert cp.capability_states(blank)["stt"] == cp.MISCONFIGURED


def test_unknown_capability_fails_loud():
    with pytest.raises(cp.ConfigError):
        cp.capability_state("no_such_capability", {})


# ── the live probe (incident 2: SET-but-rejected credentials must show as misconfigured) ─────────


def test_probe_rejection_demotes_health_row_to_misconfigured(monkeypatch):
    env = {"TRANSCRIPTION_SERVICE_URL": "http://stt", "TRANSCRIPTION_SERVICE_TOKEN": "bad-token"}
    monkeypatch.setattr(
        cp, "_run_probe",
        lambda spec, env: {"ok": False, "status": 401,
                           "reason": "unauthorized — the configured token was REJECTED by the endpoint"},
    )
    rows = cp.capability_health(env)
    assert rows["stt"]["state"] == cp.MISCONFIGURED, (
        "a SET-but-rejected STT token must surface as misconfigured on /health, not as a silent "
        "transcription-less meeting"
    )
    assert rows["stt"]["probe"]["status"] == 401


def test_probe_result_is_cached_per_ttl(monkeypatch):
    env = {"TRANSCRIPTION_SERVICE_URL": "http://stt", "TRANSCRIPTION_SERVICE_TOKEN": "t"}
    calls = []
    monkeypatch.setattr(cp, "_run_probe", lambda spec, e: (calls.append(1), {"ok": True, "status": 405})[1])
    cp.capability_health(env)
    cp.capability_health(env)
    assert len(calls) == 1, "within ttl_s the cached probe verdict is reused (no probe per health poll)"


def test_env_only_state_never_probes():
    # the spawn guard's oracle is pure — no probe I/O may ride the request path
    env = {"TRANSCRIPTION_SERVICE_URL": "http://stt", "TRANSCRIPTION_SERVICE_TOKEN": "t"}
    assert cp.capability_state("stt", env) == cp.CONFIGURED
    assert cp._probe_cache == {}


# ── /health rows (ADDITIVE) ──────────────────────────────────────────────────────────────────────


def test_health_carries_capability_rows_additively(monkeypatch):
    monkeypatch.setattr(cp, "_run_probe", lambda spec, env: {"ok": True, "status": 405})
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    # the pre-existing consumers' keys are untouched
    assert body["status"] == "ok"
    assert body["service"] == "meeting-api"
    # the additive config.v1 rows (conftest sets the STT pair → configured)
    assert body["capabilities"]["stt"]["state"] == cp.CONFIGURED
    assert body["capabilities"]["stt"]["probe"]["ok"] is True
    assert "state" in body["capabilities"]["object_storage"]


# ── the canonical gate: POST /bots consults the declared capability (incident 1 regression) ──────


def test_spawn_503_names_the_misconfigured_half(monkeypatch):
    """THE INCIDENT, upgraded: URL set but TOKEN unset used to read as 'not configured (both unset)'.
    The declaration-driven guard reports the true state (misconfigured) and names EXACTLY the unset
    key — actionable, and the same row /health shows."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test/transcribe")
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "half-stt"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    # the true tri-state + EXACTLY the unset key — actionable, and the same row /health shows
    assert "misconfigured: TRANSCRIPTION_SERVICE_TOKEN unset" in detail
    assert "transcribe_enabled=false" in detail


def test_spawn_503_when_stt_fully_unset(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "no-stt"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "not_configured" in detail
    assert "TRANSCRIPTION_SERVICE_URL" in detail and "TRANSCRIPTION_SERVICE_TOKEN" in detail
