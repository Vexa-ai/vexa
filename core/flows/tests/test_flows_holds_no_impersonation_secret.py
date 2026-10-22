"""FLOWS HOLDS NO CREDENTIAL THAT CAN ACT AS AN ARBITRARY PERSON.

Flows used to hold two such credentials. `INTERNAL_API_SECRET` opened every `/internal/*` and
`/api/*` route of every domain, believed beside any `X-User-Id` flows chose to name; the admin key
(`VEXA_FLOWS_ADMIN_KEY`) created accounts and MINTED a full-scope gateway token for any user id on
request (`/admin/users/{id}/tokens`), which flows then presented to meetings as `X-API-Key`. A
compromised flows process was therefore every person on the platform, on every route.

Flows now holds ONE credential, `VEXA_FLOWS_SERVICE_KEY`, which opens the gateway's service door
and nothing else; the door forwards only the calls on its flows allow-list. Three properties:

  (a) no module under `core/flows/src` reads the internal secret, its legacy spellings, the admin
      key or `ADMIN_TOKEN` from the environment, and the brick's config declaration names none;
  (b) with BOTH old credentials present in the environment as real-looking values, every outbound
      call flows makes carries the flows key and the subject — and neither old value, nor any of
      the old credential headers, nor an `/admin/` route, nor a token mint;
  (c) the inbound internal routes refuse a caller presenting the real internal secret, and admit
      the flows key (positive control).

Each claim carries its positive control, so a test cannot pass because nothing was recorded.
Offline: `http` is replaced by a recorder at its one seam.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

#: The names flows must never read. `ADMIN_TOKEN` is admin-api's own name for the admin key.
FORBIDDEN_ENV = ("INTERNAL_API_SECRET", "VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET",
                 "VEXA_FLOWS_ADMIN_KEY", "ADMIN_TOKEN")

#: Real-looking values: not on any placeholder list, so nothing refuses them before they could leak.
REAL_INTERNAL = "isec-7f3a91c2e8b54d06a1f9c3e27b80d5aa"
REAL_ADMIN = "adm-4b8d02e6aa19c7f35e0d81b2c6a94f70"
SERVICE_KEY = "fsk-91e0c4d7b23a58f6e1c0a9d4b7f36e25"
UID = "126"

FORBIDDEN_HEADERS = {"x-internal-secret", "x-admin-api-key", "x-api-key"}


# ── (a) the source reads none of them, and the declaration declares none of them ──────────────

def _env_reads(tree: ast.AST) -> list:
    """Every string literal that IS one of the forbidden names (a read by literal, or a `*_ENV`
    constant that a read goes through), plus every `os.environ`/`os.getenv` call naming one."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in FORBIDDEN_ENV:
            hits.append((node.lineno, node.value))
    return hits


def test_no_flows_module_reads_an_impersonation_secret_from_the_environment():
    offenders = {}
    scanned = 0
    for f in sorted(SRC.rglob("*.py")):
        scanned += 1
        hits = _env_reads(ast.parse(f.read_text(encoding="utf-8")))
        if hits:
            offenders[f.relative_to(SRC).as_posix()] = hits
    assert scanned > 20, "the scan found no source — it would pass vacuously"
    assert not offenders, f"flows reads a credential it must not hold: {offenders}"


def test_the_scan_sees_a_read_when_there_is_one():
    """Positive control for the scan above: both shapes a read takes are caught."""
    literal = 'import os\nk = os.environ.get("INTERNAL_API_SECRET")\n'
    via_constant = 'ADMIN_ENV = "VEXA_FLOWS_ADMIN_KEY"\nimport os\nk = os.getenv(ADMIN_ENV)\n'
    assert _env_reads(ast.parse(literal)) == [(2, "INTERNAL_API_SECRET")]
    assert _env_reads(ast.parse(via_constant)) == [(1, "VEXA_FLOWS_ADMIN_KEY")]
    docstring_only = '"""flows never reads INTERNAL_API_SECRET."""\n'
    assert _env_reads(ast.parse(docstring_only)) == [], "prose that names a key is not a read"


def test_the_declaration_names_none_of_them():
    decl = json.loads((SRC / "config.v1.json").read_text(encoding="utf-8"))
    declared = {k["key"] for k in decl["keys"]}
    assert "VEXA_FLOWS_SERVICE_KEY" in declared, "positive control: the flows key is declared"
    assert not declared & set(FORBIDDEN_ENV), declared & set(FORBIDDEN_ENV)
    import flows_config
    assert "VEXA_FLOWS_SERVICE_KEY" in flows_config.DECLARED
    assert not set(flows_config.DECLARED) & set(FORBIDDEN_ENV)
    assert not set(getattr(flows_config, "SECRETS", ())) & set(FORBIDDEN_ENV)


# ── (b) outbound: every call carries the flows key, and nothing of the old credentials ────────

class _Recorder:
    """`http` at its one seam. Answers just enough of each domain for every caller to finish."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, method, url, headers, body=None, timeout=20):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "body": body})
        path = url.split("?", 1)[0]
        if "/tokens" in path:                                   # a mint — must never be reached
            return 200, {"token": "minted-gateway-token"}
        if path.endswith("/settings"):
            return 200, {"timezone": "Europe/Lisbon"}
        if "by-email/" in path or "/users/email/" in path:
            return (404, {"detail": "no such user"}) if "new" in path else (200, {"id": int(UID)})
        if method == "POST" and path.endswith("/users"):
            return 201, {"id": 127, "email": "new@bank.test"}
        if path.endswith("/email") or re.search(r"/users/\d+$", path):
            return 200, {"id": int(UID), "email": "anna@bank.test"}
        if "/transcripts/" in path:
            return 200, {"segments": [{"speaker": "Anna", "text": "hello"}]}
        if method == "POST" and path.endswith("/bots"):
            return 201, {"id": 41, "native_meeting_id": "abc-defg-hij", "platform": "google_meet"}
        if path.endswith("/internal/scaffolds"):
            return 201, {"id": "s1", "url": "https://app.test/?s=s1"}
        if path.endswith("/internal/notifications"):
            return 200, {"delivered": 1}
        if "/api/workspace/file" in path:
            return 200, {"content": "# page"}
        return 200, {}


@pytest.fixture
def wire(monkeypatch):
    """Both OLD credentials present, as a deployment that never cleaned them out would have them;
    the flows key present; every door named; `http` recorded wherever it was imported by name."""
    monkeypatch.setenv("INTERNAL_API_SECRET", REAL_INTERNAL)
    monkeypatch.setenv("VEXA_INTERNAL_SECRET", REAL_INTERNAL)
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", REAL_ADMIN)
    monkeypatch.setenv("ADMIN_TOKEN", REAL_ADMIN)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://gw.test/identity")
    monkeypatch.setenv("VEXA_FLOWS_AGENT_API_URL", "http://gw.test/agent")
    monkeypatch.setenv("VEXA_FLOWS_GATEWAY_URL", "http://gw.test/meetings")
    monkeypatch.setenv("VEXA_UI_URL", "https://app.test")
    from flows_steps import common, meeting
    rec = _Recorder()
    monkeypatch.setattr(common, "http", rec)
    monkeypatch.setattr(meeting, "http", rec)
    monkeypatch.setattr(common, "AGENT_API", "http://gw.test/agent", raising=False)
    if hasattr(common, "_KEY_CACHE"):                      # the token cache the old shape kept
        common._KEY_CACHE.clear()
    if hasattr(common, "forget_person_settings"):
        common.forget_person_settings()
    return rec


def _dispatch_ctx():
    from flows import Reaction, StepCtx
    r = Reaction("rid", "sid", "meeting.started",
                 {"uid": UID, "url": "https://meet.google.com/abc-defg-hij"}, "invite_intake", 3,
                 "dispatch_bot", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:dispatch_bot", prior={"ensure_user": {"uid": UID}},
                   clock_now=1_000_000.0, scratch={})


def _ws_get():
    from flows_steps import common
    common.ws_get(UID, "people/anna.md")


def _person_settings():
    from flows_steps import common
    common.person_settings(UID)


def _platform_user_id():
    from flows_steps import common
    assert common.platform_user_id("anna@bank.test") == UID


def _ensure_platform_user():
    from flows_steps import common
    assert common.ensure_platform_user("new@bank.test") == "127"


def _address_for_uid():
    from flows_steps import common
    assert common.address_for_uid(UID) == "anna@bank.test"


def _transcript_text():
    from flows_steps import meeting
    assert meeting.transcript_text(UID, 41) == "hello"


def _dispatch_bot():
    from flows_steps import meeting
    meeting.dispatch_bot(_dispatch_ctx())


def _mint_scaffold():
    from flows_steps import common
    assert common.mint_scaffold("prep", "anna@bank.test", opening="prep",
                                meeting_id=41) == "https://app.test/?s=s1"


def _notify_in_app():
    from flows_steps import common
    assert common.notify_in_app("bot_joined", meeting_id=41, to=UID, title="Weekly") is True


#: (driver, the X-User-Id its calls must name — None where the call acts for no person)
DRIVERS = {
    "ws_get": (_ws_get, UID),
    "person_settings": (_person_settings, None),
    "platform_user_id": (_platform_user_id, None),
    "ensure_platform_user": (_ensure_platform_user, None),
    "address_for_uid": (_address_for_uid, None),
    "meeting.transcript_text": (_transcript_text, UID),
    "meeting.dispatch_bot": (_dispatch_bot, UID),
    "mint_scaffold": (_mint_scaffold, None),
    "notify_in_app": (_notify_in_app, None),
}


def _violations(call: dict) -> list:
    out = []
    headers = call["headers"]
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    path = call["url"].split("?", 1)[0]
    for value in lowered.values():
        if REAL_INTERNAL in value:
            out.append("carries the internal secret's value")
        if REAL_ADMIN in value:
            out.append("carries the admin key's value")
        if value == "minted-gateway-token":
            out.append("carries a minted gateway token")
    for name in FORBIDDEN_HEADERS & set(lowered):
        out.append(f"sends header {name}")
    if "/admin/" in path:
        out.append(f"targets an admin route {path}")
    if re.search(r"/users/[^/]+/tokens", path) or path.endswith("/tokens"):
        out.append(f"mints a token at {path}")
    if lowered.get("x-vexa-service-key") != SERVICE_KEY:
        out.append("does not carry the flows key")
    return out


@pytest.mark.parametrize("name", list(DRIVERS))
def test_no_outbound_call_carries_an_impersonation_credential(wire, name):
    driver, subject = DRIVERS[name]
    driver()
    assert wire.calls, f"{name}: positive control — the call was not recorded at all"
    bad = {f"{c['method']} {c['url']}": v for c in wire.calls if (v := _violations(c))}
    assert not bad, f"{name}: {bad}"
    if subject is not None:
        named = [c["headers"].get("X-User-Id") for c in wire.calls]
        assert named and all(n == subject for n in named), \
            f"{name}: the subject is the person the call acts for — got {named}"
    else:
        # a call that acts for nobody names nobody; identity lookups carry the subject in the path
        assert all(c["headers"].get("X-User-Id") in (None, UID) for c in wire.calls), name


def test_the_recorder_would_see_a_leak():
    """Positive control for `_violations`: each old shape is flagged, the new shape is clean."""
    old = [{"url": "http://a/admin/users/126/tokens", "headers": {"X-Admin-API-Key": REAL_ADMIN}},
           {"url": "http://a/meetings", "headers": {"X-API-Key": "minted-gateway-token"}},
           {"url": "http://a/api/chat", "headers": {"X-Internal-Secret": REAL_INTERNAL,
                                                    "X-User-Id": UID}}]
    for call in old:
        assert _violations(call), call
    new = {"url": "http://a/api/chat", "headers": {"X-Vexa-Service-Key": SERVICE_KEY,
                                                   "X-User-Id": UID}}
    assert _violations(new) == []


# ── (c) inbound: the internal secret opens no internal route ──────────────────────────────────

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-no-impersonation",
        "VEXA_FLOWS_SERVICE_KEY": SERVICE_KEY,
        "INTERNAL_API_SECRET": REAL_INTERNAL,
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://no-impersonation:unreachable@127.0.0.1:1/flows"}


@pytest.fixture
def inbound(monkeypatch):
    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api as fa
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    from fastapi.testclient import TestClient
    # The app is a singleton that may have been imported by an earlier module with other values:
    # pin both the key it checks and — for the shape that held one — the internal secret it held.
    monkeypatch.setattr(fa, "DOOR_KEY", SERVICE_KEY, raising=False)
    monkeypatch.setattr(fa, "INTERNAL_SECRET", REAL_INTERNAL, raising=False)
    return TestClient(fa.app, raise_server_exceptions=False)


#: Bodies that fail VALIDATION, so a caller admitted past auth gets 400/422 and touches no database
#: and sends no mail; a caller refused at auth gets 403 before the body is read.
SIGNAL_BAD = {"name": "email.received", "subject": UID, "data": {}}
SIGN_IN_BAD = {"to": "not-an-address", "link": "https://app.test/redeem?t=x", "minutes": 15}


@pytest.mark.parametrize("route,body", [("/events/signal", SIGNAL_BAD),
                                        ("/mail/sign-in", SIGN_IN_BAD)])
def test_the_internal_secret_opens_no_internal_route(inbound, route, body):
    r = inbound.post(route, json=body,
                     headers={"X-Internal-Secret": REAL_INTERNAL, "X-Vexa-Service": "signals"})
    assert r.status_code == 403, f"{route} admitted the internal secret: {r.status_code} {r.text}"


@pytest.mark.parametrize("route,body", [("/events/signal", SIGNAL_BAD),
                                        ("/mail/sign-in", SIGN_IN_BAD)])
def test_the_flows_key_is_admitted_past_auth(inbound, route, body):
    """Positive control: the same request with the flows key gets past the gate (and is then
    refused for its body, which is the point of sending a malformed one)."""
    r = inbound.post(route, json=body,
                     headers={"X-Vexa-Service-Key": SERVICE_KEY, "X-Vexa-Service": "signals"})
    assert r.status_code in (400, 422), f"{route}: {r.status_code} {r.text}"


@pytest.mark.parametrize("route,body", [("/events/signal", SIGNAL_BAD),
                                        ("/mail/sign-in", SIGN_IN_BAD)])
def test_a_wrong_flows_key_is_refused(inbound, route, body):
    r = inbound.post(route, json=body,
                     headers={"X-Vexa-Service-Key": REAL_INTERNAL, "X-Vexa-Service": "signals"})
    assert r.status_code == 403, f"{route}: {r.status_code} {r.text}"
