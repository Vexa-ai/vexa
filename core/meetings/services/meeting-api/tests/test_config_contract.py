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


def test_db_pool_keys_declared_defaulted():
    # #635: DB_POOL_SIZE / DB_MAX_OVERFLOW are read in meeting_api.db.engine_pool_kwargs (an env read
    # scanned by gate:config-contract), so they must be declared — class defaulted, defaults 5/10
    # matching deploy/db-budget.json (the contract leg of the triangle for the db-budget values).
    decl = cp.load_declaration()
    by_key = {k["key"]: k for k in decl["keys"]}
    for key, default in (("DB_POOL_SIZE", "5"), ("DB_MAX_OVERFLOW", "10")):
        assert key in by_key, f"{key} must be declared (it is read in meeting_api.db)"
        assert by_key[key]["class"] == "defaulted"
        assert by_key[key]["default"] == default


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


# ── the spawn gate: POST /bots trusts the transcription RESOLVER, not the env tri-state ─────────
# (#502 C1 / PR #504): the `stt` capability tri-state still drives boot preflight + /health, but
# the spawn path now gates on what request_bot actually resolves (Settings backend > env) — the
# env-only capability check could never be satisfied by wizard-written Settings config.


def test_spawn_accepts_url_without_token(monkeypatch):
    """Semantics shift from the old capability gate: a URL with no token is a SPAWNABLE backend
    (the token belongs to the backend and may legitimately be empty — e.g. an unauthenticated
    self-hosted STT). /health's `stt` row still reads `misconfigured` for the env pair; the spawn
    gate no longer refuses on it."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test/transcribe")
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "half-stt"})
    assert r.status_code == 201, f"{r.status_code} {r.text}"


def test_spawn_503_when_stt_fully_unset(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_API_URL", raising=False)  # no Settings backend either
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "google_meet", "native_meeting_id": "no-stt"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    # the typed resolver reason — actionable for BOTH config paths (wizard Settings and env)
    assert "no transcription backend configured" in detail
    assert "Settings" in detail
    assert "TRANSCRIPTION_SERVICE_URL" in detail and "TRANSCRIPTION_SERVICE_TOKEN" in detail
    # #504 review finding 1: the refusal fires BEFORE the meeting-row write — a refused spawn
    # leaves no orphaned `requested` row, so the post-config retry cannot 409 on the dedup guard.
    assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test/transcribe")
    r2 = _client(repo).post("/bots", headers=HEADERS,
                            json={"platform": "google_meet", "native_meeting_id": "no-stt"})
    assert r2.status_code == 201, f"retry after configuring must not 409/503: {r2.status_code} {r2.text}"


# ── C1/C4 (#511): the probe's ORACLE — a wrong URL must not prove a working backend ──────────────
# These exercise the REAL _http_probe against a local HTTP server (not the _run_probe monkeypatch
# seam above), because the defect under test IS the status→verdict mapping and the URL it builds.


class _ProbeServer:
    """A local HTTP server that answers a scripted status per request path, and records the paths
    it was asked for (so a double-pathed URL is visible, not merely inferred from a 404)."""

    def __init__(self, routes: dict, default_status: int = 404):
        self.routes = routes
        self.default_status = default_status
        self.paths: list = []
        self.headers_seen: dict = {}
        self.last_body: bytes = b""
        self._server = None
        self._thread = None

    def __enter__(self):
        import http.server
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's interface
                outer.paths.append(self.path)
                outer.headers_seen = dict(self.headers)
                outer.last_body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(outer.routes.get(self.path, outer.default_status))
                self.end_headers()

            def log_message(self, *a):  # keep the test output clean
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


_STT_PATH = "/v1/audio/transcriptions"


def _stt_probe_spec():
    return cp.load_declaration()["capabilities"]["stt"]["probe"]


def test_probe_url_accepts_both_declared_url_shapes():
    """C4: the ONE rule — append the path only when the configured URL does not already carry it.
    Same rule as the bot's client (whisper/src/transcription-client.ts) and the dictation route."""
    assert cp.probe_url("https://api.openai.com", _STT_PATH) == f"https://api.openai.com{_STT_PATH}"
    assert cp.probe_url("https://api.openai.com/", _STT_PATH) == f"https://api.openai.com{_STT_PATH}"
    # the full endpoint URL — appending blindly here is the double-path 404 this closes
    full = f"https://api.openai.com{_STT_PATH}"
    assert cp.probe_url(full, _STT_PATH) == full
    assert cp.probe_url(full + "/", _STT_PATH) == full


def test_probe_404_is_misconfigured_not_ok():
    """C1 (A1): a URL whose transcriptions path answers 404 is the WRONG address — it must not
    probe green. A real OpenAI-compatible endpoint answers 400/401 to an empty body, never 404."""
    with _ProbeServer(routes={}, default_status=404) as srv:  # every path 404s
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "tok"}
        result = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
    assert result["ok"] is False, "404 must NOT count as proof of a reachable transcriptions endpoint"
    assert result["status"] == 404
    assert "URL shape" in result["reason"]


def test_probe_404_demotes_the_health_row():
    """C1 (A1), at the surface an operator actually reads: /health's stt row goes misconfigured."""
    with _ProbeServer(routes={}, default_status=404) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "tok"}
        rows = cp.capability_health(env)
    assert rows["stt"]["state"] == cp.MISCONFIGURED
    assert rows["stt"]["probe"]["status"] == 404


def test_probe_400_and_401_verdicts_unchanged():
    """C1 no-regression: 400 stays proof-of-life (green); 401 stays a rejected credential (red)."""
    with _ProbeServer(routes={_STT_PATH: 400}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "tok"}
        ok = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
    assert ok["ok"] is True and ok["status"] == 400, "an empty-body 400 proves a live endpoint"

    with _ProbeServer(routes={_STT_PATH: 401}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "bad"}
        rejected = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
    assert rejected["ok"] is False and rejected["status"] == 401
    assert "REJECTED" in rejected["reason"]


def test_probe_accepts_a_full_path_url_without_double_pathing():
    """C4 (A5): the SAME URL that works in a meeting must probe green. Configured as the full
    endpoint, the probe must request that path once — not append a second copy into a 404."""
    with _ProbeServer(routes={_STT_PATH: 400}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base + _STT_PATH,
               "TRANSCRIPTION_SERVICE_TOKEN": "tok"}
        result = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
        requested = list(srv.paths)
    assert result["ok"] is True, f"full-path URL must not double-path: requested {requested}"
    assert requested == [_STT_PATH], f"expected exactly one un-doubled request, got {requested}"


# ── C3 (#511): a spawn against a SET-but-BROKEN backend refuses with the probe's reason ─────────


def _seed_stt_verdict(result: dict):
    """Put a verdict in the probe cache the way boot preflight / a /health poll would."""
    import time as _t
    cp._probe_cache["stt"] = {"at": _t.monotonic(), "result": result}


def test_spawn_503_when_the_probe_says_the_backend_is_broken(monkeypatch):
    """A3: set env + a cached FAILING verdict ⇒ typed 503 carrying the probe's reason, and NO
    meeting row (the refusal precedes the write, so the retry after a fix cannot 409)."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "bad-token")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)  # no Settings backend → env is the resolved one
    _seed_stt_verdict({"ok": False, "status": 401, "kind": "unauthorized",
                       "reason": "unauthorized — the configured token was REJECTED by the endpoint"})
    repo = InMemoryMeetingRepo()
    r = _client(repo).post("/bots", headers=HEADERS,
                           json={"platform": "google_meet", "native_meeting_id": "broken-stt"})
    assert r.status_code == 503, f"{r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert "REJECTED" in detail, f"the 503 must carry the probe's own reason: {detail}"
    assert "/health?force=1" in detail, "the refusal must tell the operator how to re-test now"
    assert repo._meetings == {}, f"refused spawn wrote a meeting row: {repo._meetings}"


def test_spawn_proceeds_when_the_probe_says_the_backend_works(monkeypatch):
    """A3 no-regression: a passing verdict must not interfere with the spawn."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "good-token")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    _seed_stt_verdict({"ok": True, "status": 400})
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "working-stt"})
    assert r.status_code == 201, f"{r.status_code} {r.text}"


def test_spawn_proceeds_when_no_verdict_has_been_cached(monkeypatch):
    """An UNPROBED capability is not a broken one — absence of a verdict must never refuse."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    assert cp._probe_cache == {}
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "unprobed-stt"})
    assert r.status_code == 201, f"{r.status_code} {r.text}"


def test_stale_verdict_does_not_block_a_fixed_backend(monkeypatch):
    """The stale-red fork the issue names: past the probe's ttl the cache holds no actionable
    opinion, so an operator who just fixed the endpoint is not locked out by the old red."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "now-fixed")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    import time as _t
    cp._probe_cache["stt"] = {"at": _t.monotonic() - 3600,
                              "result": {"ok": False, "kind": "unauthorized",
                                         "reason": "unauthorized — ...", "status": 401}}
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "stale-red"})
    assert r.status_code == 201, f"a verdict older than the ttl must not refuse: {r.text}"


def test_settings_backend_is_not_blocked_by_the_env_backends_verdict(monkeypatch):
    """The claim-together fork (#502 C1): the cached verdict describes the ENV endpoint. When the
    user's Settings backend is what we resolved, the env backend's health is irrelevant — blocking
    on it would refuse a spawn against a perfectly good endpoint."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://env-stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "env-token")
    _seed_stt_verdict({"ok": False, "status": 401, "kind": "unauthorized",
                       "reason": "unauthorized — env backend is broken"})

    async def _settings_backend(user_id):
        return {"url": "http://user-stt.test", "token": "user-token"}

    from meeting_api.bot_spawn import service as svc
    monkeypatch.setattr(svc, "_resolve_transcription_backend", _settings_backend)
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "settings-stt"})
    assert r.status_code == 201, f"the env verdict must not gate a Settings backend: {r.text}"


def test_unreachable_backend_does_not_refuse_spawns(monkeypatch):
    """A DOWN endpoint is not a MISCONFIGURED one. Refusing on `unreachable` would couple every
    spawn to STT liveness — an STT restart or a DNS blip would 503 the whole deployment for a
    minute, while the bot's own transcription client already retries. Only a fault an operator
    must FIX (rejected token / wrong path) may refuse."""
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok")
    monkeypatch.delenv("ADMIN_API_URL", raising=False)
    _seed_stt_verdict({"ok": False, "kind": "unreachable",
                       "reason": "unreachable: URLError: <urlopen error [Errno -2] Name or "
                                 "service not known>"})
    r = _client().post("/bots", headers=HEADERS,
                       json={"platform": "google_meet", "native_meeting_id": "stt-down"})
    assert r.status_code == 201, f"a down backend must not refuse the spawn: {r.text}"


def test_probe_failure_kinds_are_classified():
    """The kind is what lets a consumer tell 'fix your config' from 'the endpoint is down'."""
    spec = _stt_probe_spec()["http"]
    with _ProbeServer(routes={}, default_status=404) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "t"}
        assert cp._http_probe(spec, env, timeout=5)["kind"] == "invalid_endpoint"
    with _ProbeServer(routes={_STT_PATH: 401}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "t"}
        assert cp._http_probe(spec, env, timeout=5)["kind"] == "unauthorized"
    # nothing listening on a closed port — a liveness fault, never a config fault
    env = {"TRANSCRIPTION_SERVICE_URL": "http://127.0.0.1:1", "TRANSCRIPTION_SERVICE_TOKEN": "t"}
    down = cp._http_probe(spec, env, timeout=2)
    assert down["kind"] == "unreachable"
    assert down["kind"] not in cp.CONFIG_FAULT_KINDS
    assert {"unauthorized", "invalid_endpoint", "exhausted"} == set(cp.CONFIG_FAULT_KINDS)


def test_probe_402_is_exhausted_config_fault():
    """The 2026-07-19 recurrence: a token that AUTHENTICATES but 402s every transcription probed
    green (the old empty-body probe could never even elicit the 402). The declared
    exhausted_statuses turn it into a refusable CONFIG fault with the consequence in the reason."""
    spec = _stt_probe_spec()["http"]
    with _ProbeServer(routes={_STT_PATH: 402}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "t"}
        result = cp._http_probe(spec, env, timeout=5)
    assert result["ok"] is False and result["kind"] == "exhausted"
    assert result["kind"] in cp.CONFIG_FAULT_KINDS, "spawn must refuse on an exhausted token"
    assert "no transcript" in result["reason"].lower()


def test_probe_sends_real_audio_so_a_metered_backend_can_price_it():
    """The declaration says payload: audio — assert the request BODY actually carries a WAV in
    multipart form-data. An empty POST is answered 400/422 for funded and worthless credentials
    alike, which is exactly how the exhausted token used to probe green."""
    spec = _stt_probe_spec()["http"]
    assert spec.get("payload") == "audio", "stt must declare the audio round-trip"
    assert 402 in (spec.get("exhausted_statuses") or []), "stt must declare 402 as exhausted"
    bodies: list = []

    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            bodies.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {"TRANSCRIPTION_SERVICE_URL": f"http://127.0.0.1:{server.server_address[1]}",
               "TRANSCRIPTION_SERVICE_TOKEN": "t"}
        assert cp._http_probe(spec, env, timeout=10)["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(bodies) == 1 and b"RIFF" in bodies[0] and b"probe.wav" in bodies[0]


def test_probe_declares_a_long_ttl_because_the_round_trip_is_metered():
    probe = _stt_probe_spec()
    assert float(probe.get("ttl_s") or 0) >= 600, (
        "the audio probe COSTS a fraction of a minute per run — /health must not re-bill it "
        "every 60s")


def test_probe_sends_a_named_user_agent():
    """Cloudflare-fronted backends (Groq, Together) 1010-ban the default Python-urllib
    signature BEFORE auth runs; the 403 then reads `unauthorized` and the spawn gate
    refuses every capture against a working backend (first real staging capture)."""
    with _ProbeServer(routes={_STT_PATH: 200}) as srv:
        env = {"TRANSCRIPTION_SERVICE_URL": srv.base, "TRANSCRIPTION_SERVICE_TOKEN": "tok"}
        result = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
    assert result["ok"] is True
    ua = srv.headers_seen.get("User-Agent", "")
    assert ua and "python-urllib" not in ua.lower(), ua


def test_probe_model_follows_the_configured_backend_model():
    """The probe must speak the model id the runtime client speaks: Together answers 404
    for `whisper-1` (a name it does not serve), and 404 reads `invalid_endpoint` — a
    config fault that refuses spawns against a perfectly working backend."""
    with _ProbeServer(routes={_STT_PATH: 200}) as srv:
        env = {
            "TRANSCRIPTION_SERVICE_URL": srv.base,
            "TRANSCRIPTION_SERVICE_TOKEN": "tok",
            "TRANSCRIPTION_MODEL": "openai/whisper-large-v3",
        }
        result = cp._http_probe(_stt_probe_spec()["http"], env, timeout=5)
    assert result["ok"] is True
    assert b"openai/whisper-large-v3" in srv.last_body
    assert b"whisper-1" not in srv.last_body
