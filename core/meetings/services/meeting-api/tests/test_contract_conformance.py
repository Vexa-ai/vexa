"""gate:contract-conformance — the SHIPPED meeting-api conforms to the sealed api.v1 (P8).

The contract (``core/gateway/contracts/api.v1/api.schema.json``, "Vexa API Gateway" 1.5.0) is the
spec; the gateway proxies the public surface VERBATIM to meeting-api, so meeting-api is the
implementation behind every meeting-domain api.v1 route. ``gate:schema`` proves goldens ≡ schema and
``gate:contract-version`` freezes the seal, but NOTHING proved the *running service* conforms to the
sealed surface — and it drifted: api.v1 declares routes meeting-api never implemented (A1/A2 in
MATURITY-FINDINGS.md), and the conformance harness drove a FAKE meeting-api (gateway_conformance/
fake_meeting_api.py) that implements them, MASKING the gap. The fake conformed; the real app did not.

This is the OFFLINE STRUCTURAL half: import the real ``create_app()``, enumerate its registered
routes, and compare the (path, method) set against the sealed api.v1 CORE surface, asserting:

  (a) every api.v1-covered route the real app IMPLEMENTS conforms to a declared (path, method) —
      a route that drifts from the contract's spelling is a bug; and
  (b) every api.v1 CORE/named route is EITHER implemented OR on the explicit, reasoned ``WAIVED``
      list below — so KNOWN drift is bounded + documented, and NEW unwaived drift turns the gate RED.

DO NOT widen by editing api.v1 or the seal — reconciling the contract itself (implement the route, or
remove it from api.v1) is a human-gated ``lane:contract`` change (P4); the waiver is the correct
interim record of the gap (ADR-0022: source states the designed present; the gap lives in the ledger).

L4 EXTENSION (bbb, out of scope here): live input-fuzzing — schemathesis run against the RUNNING
gateway's OpenAPI to prove request/response BODIES conform under hostile input — is the dynamic half
that needs the real stack. This gate is the offline structural half (routes registered ≡ routes declared).
"""
from __future__ import annotations

import json
from pathlib import Path

from meeting_api import create_app


# ── the sealed api.v1 surface meeting-api is the implementation behind ────────────────────────────
# The CORE (path, method) pairs the gateway forwards to meeting-api (== api.v1/validate.mjs's CORE set
# + the named drift findings the contract still declares). Other api.v1 routes are out of meeting-api's
# responsibility — they forward to admin-api (/admin), agent-api (/api/*), the mcp service (/mcp), or
# are gateway-local (/auth/me, /b/{token}, /public/transcripts) — so they are NOT meeting-api's to serve.
API_V1_MEETING_SURFACE: set[tuple[str, str]] = {
    ("GET", "/bots"),
    ("POST", "/bots"),
    ("GET", "/bots/status"),
    ("GET", "/bots/id/{meeting_id}"),
    ("DELETE", "/bots/{platform}/{native_meeting_id}"),
    ("PUT", "/bots/{platform}/{native_meeting_id}/config"),
    ("POST", "/bots/{platform}/{native_meeting_id}/speak"),
    ("GET", "/transcripts/{platform}/{native_meeting_id}"),
    ("GET", "/recordings"),
    ("GET", "/recordings/{recording_id}"),
    ("DELETE", "/recordings/{recording_id}"),
    ("GET", "/meetings"),
}

# ── the EXPLICIT, REASONED waiver list — known, bounded drift the contract declares but the shipped
# meeting-api does not (yet) implement. Each entry is (METHOD, api.v1-path) → why it is deferred. A NEW
# unimplemented route NOT on this list turns the gate RED; closing a gap means implementing the route
# (and DELETING its waiver), or removing it from api.v1 in a lane:contract PR (then deleting the waiver).
WAIVED: dict[tuple[str, str], str] = {
    ("GET", "/bots/id/{meeting_id}"):
        "api.v1 declares it (desc: 'Forward to meeting-api GET /bots/{meeting_id}') but the gateway "
        "actually maps GET /meetings/{meeting_id} → meeting-api GET /meetings/{meeting_id}; the "
        "dashboard meeting-detail reads /meetings/{id}. Deferred reconcile (drop or implement) is a "
        "lane:contract change (A1/A2, MATURITY-FINDINGS.md).",
    ("DELETE", "/recordings/{recording_id}"):
        "api.v1 declares recording delete; not implemented in the v0.12 carve (no consumer wired "
        "it — delete rides DELETE /meetings/{p}/{n}, which purges the meeting's recordings). "
        "Deferred reconcile (A2, MATURITY-FINDINGS.md).",
    ("PUT", "/bots/{platform}/{native_meeting_id}/config"):
        "Voice/config command to an ACTIVE bot (language/task update) — a bot-command-channel path, "
        "not meeting-api persistence. Deferred with the voice-agent carve; the gateway forwards it.",
    ("POST", "/bots/{platform}/{native_meeting_id}/speak"):
        "Voice Agent TTS — a bot-command-channel path (acts.v1 PUBLISH), not meeting-api persistence. "
        "Deferred with the voice-agent carve; the gateway forwards it.",
}


def _api_v1_declared() -> set[tuple[str, str]]:
    """The (METHOD, path) pairs the SEALED api.v1 contract declares (read by path, the seam — P8)."""
    rel = Path("core") / "gateway" / "contracts" / "api.v1" / "api.schema.json"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            spec = json.loads((parent / rel).read_text())
            return {
                (method.upper(), path)
                for path, item in spec.get("paths", {}).items()
                for method in item
                if method.upper() != "OPTIONS"
            }
    raise FileNotFoundError("monorepo root with core/gateway/contracts/api.v1/api.schema.json not found")


def _implemented() -> set[tuple[str, str]]:
    """The (METHOD, path) pairs the SHIPPED meeting-api app registers (via the generated OpenAPI —
    flattens every mounted router; framework + internal/unschematized routes excluded)."""
    spec = create_app().openapi()
    skip = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    return {
        (method.upper(), path)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.upper() != "OPTIONS" and path not in skip
    }


def test_surface_set_is_subset_of_the_sealed_contract():
    """Guard the gate's own scope: every route in API_V1_MEETING_SURFACE is genuinely declared by the
    sealed api.v1 (so the gate can't silently check a route the contract doesn't actually have)."""
    declared = _api_v1_declared()
    stray = API_V1_MEETING_SURFACE - declared
    assert not stray, f"surface set names routes api.v1 does not declare (stale gate scope): {sorted(stray)}"


def test_implemented_meeting_routes_conform_to_a_declared_route():
    """(a) Every api.v1-covered route the real app implements matches a DECLARED (path, method) — a
    route that drifts from the contract's exact spelling is a bug."""
    declared = _api_v1_declared()
    implemented = _implemented()
    # Restrict to the meeting-domain surface (the app also serves /health + internal callbacks, which
    # api.v1 does not declare — those are correctly out of the public-contract comparison).
    covered = {r for r in implemented if r in API_V1_MEETING_SURFACE}
    drifted = covered - declared
    assert not drifted, (
        "meeting-api registers a route that does NOT match its api.v1 spelling "
        f"(implementation/contract drift): {sorted(drifted)}"
    )


def test_every_declared_meeting_route_is_implemented_or_explicitly_waived():
    """(b) Every api.v1 route in meeting-api's responsibility set is EITHER implemented OR on the
    explicit WAIVED list. A NEW unwaived gap → RED. Known drift stays bounded + documented."""
    implemented = _implemented()
    gaps = sorted(r for r in API_V1_MEETING_SURFACE if r not in implemented and r not in WAIVED)
    assert not gaps, (
        "api.v1 declares route(s) that meeting-api neither implements NOR waives — either implement "
        "the route, or add a reasoned WAIVED entry (and reconcile the contract in a lane:contract PR): "
        f"{gaps}"
    )


def test_no_stale_waivers():
    """A waiver for a route that IS now implemented (or no longer in the surface) is stale — delete it.
    Keeps the bounded-drift ledger honest (a waiver must name a REAL, current gap)."""
    implemented = _implemented()
    stale = sorted(
        r for r in WAIVED if r not in API_V1_MEETING_SURFACE or r in implemented
    )
    assert not stale, (
        "WAIVED lists route(s) that are now implemented or out of scope — remove the stale waiver(s): "
        f"{stale}"
    )


def test_waivers_carry_a_reason():
    """Each waiver states WHY the drift is deferred (a bare waiver is undocumented drift)."""
    bare = sorted(r for r, why in WAIVED.items() if not why or not why.strip())
    assert not bare, f"waiver(s) without a reason: {bare}"
