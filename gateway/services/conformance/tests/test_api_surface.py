"""O-API-1 (api.v1) — behavioral conformance of the sealed public REST surface.

Drives each CORE path (the ones api.v1/validate.mjs asserts) against the TestClient
gateway + port-fake meeting-api, OFFLINE. For every path:
  * a request WITH a valid x-api-key → 2xx, and the body conforms to its sealed
    ``#/components/schemas/<Shape>`` (validated by path via jsonschema), and
  * a request WITHOUT x-api-key → 401 (auth-negative, fail-closed).

No docker, no real backend, no meetings — the gateway proxies to an in-process fake that
replays the frozen goldens.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gateway_conformance.contracts import (
    api_component_validator,
    api_core_paths,
    api_schema,
    assert_api_conforms,
)
from gateway_conformance.fake_meeting_api import VALID_API_KEY
from gateway_conformance.gateway_app import build_gateway

AUTH = {"x-api-key": VALID_API_KEY}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(build_gateway())


# (method, concrete-url, expected-2xx-status, sealed-component-or-None)
# component=None where api.v1 has no sealed component for that body (recordings list) —
# we still assert 2xx + JSON, just not against a frozen shape.
CORE_CASES = [
    ("GET", "/bots", 200, "MeetingListResponse"),
    ("POST", "/bots", 201, "MeetingResponse"),
    ("GET", "/bots/status", 200, "BotStatusResponse"),
    ("DELETE", "/bots/google_meet/abc-defg-hij", 200, "MeetingResponse"),
    ("PUT", "/bots/google_meet/abc-defg-hij/config", 202, "MeetingResponse"),
    ("POST", "/bots/google_meet/abc-defg-hij/speak", 200, "MeetingResponse"),
    ("GET", "/transcripts/google_meet/abc-defg-hij", 200, "TranscriptionResponse"),
    ("GET", "/recordings", 200, None),
    ("GET", "/recordings/1", 200, None),
    ("GET", "/meetings", 200, "MeetingListResponse"),
]


def _request(client: TestClient, method: str, url: str, headers: dict):
    body = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"} if method == "POST" else None
    return client.request(method, url, headers=headers, json=body)


def test_core_paths_match_validate_mjs():
    """The CORE cases this eval drives are EXACTLY the (path, method) pairs the sealed
    api.v1/validate.mjs asserts — drift between the two is a bug."""
    sealed = set(api_core_paths())
    driven = {
        # map concrete urls back to the templated sealed path
        ("/bots", "get"), ("/bots", "post"), ("/bots/status", "get"),
        ("/bots/{platform}/{native_meeting_id}", "delete"),
        ("/bots/{platform}/{native_meeting_id}/config", "put"),
        ("/bots/{platform}/{native_meeting_id}/speak", "post"),
        ("/transcripts/{platform}/{native_meeting_id}", "get"),
        ("/recordings", "get"), ("/recordings/{recording_id}", "get"),
        ("/meetings", "get"),
    }
    assert driven == sealed, f"driven set != sealed CORE set: {driven ^ sealed}"
    assert len(driven) == 10


@pytest.mark.parametrize("method,url,ok_status,component", CORE_CASES,
                         ids=[f"{m} {u}" for m, u, _, _ in CORE_CASES])
def test_authed_response_conforms(client, method, url, ok_status, component):
    """WITH a valid key: 2xx, and the body conforms to its sealed component schema."""
    resp = _request(client, method, url, AUTH)
    assert resp.status_code == ok_status, f"{method} {url} → {resp.status_code}: {resp.text}"
    payload = resp.json()
    if component is not None:
        # Validate BY PATH against the frozen #/components/schemas/<component>.
        assert_api_conforms(component, payload)


def test_malformed_downstream_body_is_caught_by_conformance(monkeypatch):
    """NEGATIVE — the body-conformance leg is NOT a rubber stamp. If the downstream returns a body
    that VIOLATES the sealed component (here: `running_bots` unwrapped to a single object instead
    of the required array — a realistic meeting-api defect), the SAME validator the positive test
    trusts DETECTS it through the gateway proxy. Without this, `test_authed_response_conforms` only
    re-asserts a golden conforms to its own schema (tautological); this proves a real defect fails."""
    from gateway_conformance import fake_meeting_api as fma

    real_golden = fma._golden

    def corrupt(name):
        data = real_golden(name)
        if name == "BotStatusResponse.example.json":
            return {"running_bots": data["running_bots"][0]}  # object, not array → type violation
        return data

    monkeypatch.setattr(fma, "_golden", corrupt)
    c = TestClient(build_gateway())
    resp = c.get("/bots/status", headers=AUTH)
    assert resp.status_code == 200, f"gateway should proxy the body verbatim: {resp.status_code}"
    body = resp.json()
    assert isinstance(body["running_bots"], dict), "the malformed (unwrapped) shape should arrive verbatim"
    # the SAME oracle the positive leg trusts now FAILS — the conformance check can catch a real defect
    with pytest.raises(AssertionError):
        assert_api_conforms("BotStatusResponse", body)
    assert list(api_component_validator("BotStatusResponse").iter_errors(body)), \
        "validator must report errors on the malformed body"


@pytest.mark.parametrize("method,url,ok_status,component", CORE_CASES,
                         ids=[f"{m} {u}" for m, u, _, _ in CORE_CASES])
def test_unauthenticated_request_is_rejected(client, method, url, ok_status, component):
    """AUTH-NEGATIVE: no x-api-key → 401 (fail-closed). Mirrors main.forward_request."""
    resp = _request(client, method, url, headers={})
    assert resp.status_code == 401, f"{method} {url} unauth → {resp.status_code} (expected 401)"
    assert resp.json().get("detail") == "Missing API key"


def test_invalid_api_key_is_rejected(client):
    """A present-but-bogus key → 401 Invalid API key (token does not validate)."""
    resp = client.get("/bots/status", headers={"x-api-key": "vxa_not_a_real_key"})
    assert resp.status_code == 401
    assert resp.json().get("detail") == "Invalid API key"


def test_insufficient_scope_is_rejected():
    """SCOPE-NEGATIVE: a token lacking the route scope → 403. Mirrors main ROUTE_SCOPES."""
    # Build a gateway whose fake admin-api hands back a tx-only token, then hit a /bots route.
    from gateway_conformance import fake_meeting_api as fma

    original = dict(fma.VALID_USER)
    try:
        fma.VALID_USER["scopes"] = ["tx"]  # no 'bot'/'browser' → /bots forbidden
        c = TestClient(build_gateway())
        resp = c.get("/bots/status", headers=AUTH)
        assert resp.status_code == 403, f"tx-only key on /bots → {resp.status_code} (expected 403)"
        assert "scope" in resp.json().get("detail", "").lower()
    finally:
        fma.VALID_USER.clear()
        fma.VALID_USER.update(original)


def test_goldens_conform_by_path():
    """Belt-and-braces: the on-disk api.v1 goldens themselves conform to their sealed
    components (loaded BY PATH) — the same assertion validate.mjs makes, in Python."""
    cases = {
        "BotStatusResponse": "BotStatusResponse.example.json",
        "MeetingListResponse": "MeetingListResponse.example.json",
        "MeetingResponse": "MeetingResponse.example.json",
        "TranscriptionResponse": "TranscriptionResponse.example.json",
        "TranscriptionSegment": "TranscriptionSegment.example.json",
    }
    import json
    from gateway_conformance.contracts import CONTRACTS_DIR

    golden_dir = CONTRACTS_DIR / "api.v1" / "golden"
    for shape, fname in cases.items():
        data = json.loads((golden_dir / fname).read_text())
        assert_api_conforms(shape, data)


def test_sealed_identity_is_main_1_5_0():
    """Sanity: the schema we validate against is main's api-gateway 1.5.0 (not invented)."""
    oas = api_schema()
    assert oas.get("info", {}).get("title") == "Vexa API Gateway"
    assert oas.get("info", {}).get("version") == "1.5.0"
    # every CORE shape we assert against must exist as a sealed component
    for shape in ("BotStatusResponse", "MeetingListResponse", "TranscriptionResponse"):
        assert api_component_validator(shape) is not None
