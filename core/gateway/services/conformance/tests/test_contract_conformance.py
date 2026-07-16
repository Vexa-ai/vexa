"""gate:contract-conformance — REVERSE (contract ⊆ impl) conformance of the SEALED api.v1 (P8, #591).

`contracts.seal.json` freezes api.v1 to a FILE hash, and the pre-existing conformance proved only the
forward direction (impl ⊆ contract: every route the service IMPLEMENTS matches a declared spelling).
That one-directional check is exactly how 0.12 renamed/dropped SIX sealed endpoints and CI stayed green
(#591): a sealed (path, method) that NO service implements was never asserted, so "sealed api.v1" did
not mean "the implementation serves these routes" — a legitimate 0.10 client 404'd across the board.

This is the REVERSE half. For EVERY (path, method) the sealed ``api.v1/api.schema.json`` declares, it
asserts the UNION of the two apps that own the public REST surface — the SHIPPED gateway edge
(``gateway.create_app`` via ``build_gateway``) and the SHIPPED meeting-api (``meeting_api.create_app``,
the downstream the gateway forwards to) — registers it, OR the route is explicitly accounted for in the
audited ledger ``core/gateway/contracts/api.v1/KNOWN_GAPS.json``:

  * ``owned_elsewhere`` — a prefix forwarded to ANOTHER service (admin-api, agent-api, the mcp service)
    or a gateway-local concern; NOT this pair's responsibility. Reported LOUDLY as ``OWNED-ELSEWHERE``,
    never silently dropped.
  * ``known_gaps`` — a specific (method, path) the 0.12 core genuinely cannot serve (no backend). Reported
    LOUDLY as ``SEALED-BUT-WAIVED``. Each row carries a reason + issue link.

A sealed route that is NEITHER implemented, NOR owned-elsewhere, NOR a known gap turns the gate RED and is
listed by name — so a renamed/dropped sealed endpoint cannot slip through again. Adding a ledger row is a
deliberate, diff-visible change in the sealed contracts dir (KNOWN_GAPS.json is not a ``*.schema.json``, so
it does NOT move the api.v1 seal hash — the seal stays frozen; the gap lives in the audited ledger, ADR-0022).

It ALSO drives the frozen golden EXAMPLES against the REAL response so a field RENAME fails, not just a
path removal: ``BotStatusResponse.example.json`` carries ``running_bots``; 0.12 ``collector/app.py`` returned
``running``. Driving the real ``GET /bots/status`` and asserting the golden's field is PRESENT is the check
that catches that live seal-shape violation (A2, #591).

DO NOT widen by editing api.v1 or the seal — reconciling the contract (implement the route, or drop it from
api.v1) is a human-gated ``lane:contract`` change (P4). The ledger is the correct interim record of the gap.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway_conformance.gateway_app import build_gateway


# ── the sealed contract + audited ledger, read BY PATH (the seam — P8) ────────────────────────────
def _repo_root() -> Path:
    rel = Path("core") / "gateway" / "contracts" / "api.v1" / "api.schema.json"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return parent
    raise FileNotFoundError("monorepo root with core/gateway/contracts/api.v1/api.schema.json not found")


_CONTRACTS = _repo_root() / "core" / "gateway" / "contracts" / "api.v1"
_API_SCHEMA = _CONTRACTS / "api.schema.json"
_KNOWN_GAPS = _CONTRACTS / "KNOWN_GAPS.json"
_GOLDEN = _CONTRACTS / "golden"

_FRAMEWORK = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _api_v1_declared() -> set[tuple[str, str]]:
    spec = json.loads(_API_SCHEMA.read_text(encoding="utf-8"))
    return {
        (method.upper(), path)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.upper() not in ("OPTIONS", "HEAD", "PARAMETERS")
    }


def _routes_of(app) -> set[tuple[str, str]]:
    """(METHOD, path) the app registers, via its generated OpenAPI (flattens every mounted router)."""
    spec = app.openapi()
    return {
        (method.upper(), path)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if method.upper() not in ("OPTIONS", "HEAD", "PARAMETERS") and path not in _FRAMEWORK
    }


def _implemented_union() -> set[tuple[str, str]]:
    """The UNION of the two apps that own the public api.v1 REST surface: the shipped gateway edge and
    the shipped meeting-api it forwards to. A sealed route implemented by EITHER is covered."""
    from meeting_api import create_app as meeting_api_create_app  # noqa: WPS433 (env-local import)

    return _routes_of(build_gateway()) | _routes_of(meeting_api_create_app())


def _load_ledger() -> dict:
    """The audited KNOWN_GAPS.json. ABSENT (e.g. on pre-#591 main) ⇒ empty ledger ⇒ EVERY sealed gap is
    unwaived and the gate goes RED listing them — that is the negative control (A1), not a skip."""
    if not _KNOWN_GAPS.is_file():
        return {"owned_elsewhere": {"prefixes": []}, "known_gaps": [], "shape_gaps": []}
    return json.loads(_KNOWN_GAPS.read_text(encoding="utf-8"))


def _owned_elsewhere_prefixes(ledger: dict) -> list[dict]:
    return ledger.get("owned_elsewhere", {}).get("prefixes", [])


def _is_owned_elsewhere(path: str, prefixes: list[dict]) -> bool:
    for entry in prefixes:
        pre = entry.get("prefix")
        if not pre:
            continue
        if entry.get("exact"):
            if path == pre:
                return True
        elif path == pre or path.startswith(pre + "/"):
            return True
    return False


def _known_gap_set(ledger: dict) -> set[tuple[str, str]]:
    return {(g["method"].upper(), g["path"]) for g in ledger.get("known_gaps", [])}


# ── (b) the REVERSE assertion: contract ⊆ impl ────────────────────────────────────────────────────
def test_every_sealed_api_v1_route_is_implemented_or_audited():
    """For EVERY (path, method) the sealed api.v1 declares, the gateway+meeting-api union implements it,
    OR it is audited in KNOWN_GAPS.json (owned-elsewhere / known-gap). A sealed route that is none of
    those → RED, listed by name. This is the check that makes "sealed" mean the impl serves the route."""
    declared = _api_v1_declared()
    implemented = _implemented_union()
    ledger = _load_ledger()
    prefixes = _owned_elsewhere_prefixes(ledger)
    known = _known_gap_set(ledger)

    waived, owned_elsewhere, unaudited = [], [], []
    for method, path in sorted(declared):
        if (method, path) in implemented:
            continue
        if (method, path) in known:
            waived.append((method, path))
        elif _is_owned_elsewhere(path, prefixes):
            owned_elsewhere.append((method, path))
        else:
            unaudited.append((method, path))

    # Report the audited gaps LOUDLY (visible in the gate's stdout on every green run).
    for method, path in owned_elsewhere:
        print(f"OWNED-ELSEWHERE (out of gateway+meeting-api scope): {method} {path}")
    for method, path in waived:
        print(f"SEALED-BUT-WAIVED (no 0.12 backend — see KNOWN_GAPS.json): {method} {path}")

    assert not unaudited, (
        "SEALED api.v1 route(s) that NO service implements and that are NOT audited in "
        "core/gateway/contracts/api.v1/KNOWN_GAPS.json — a sealed endpoint was renamed or dropped. "
        "RESTORE the route, or (deliberately) add a reasoned KNOWN_GAPS.json row + reconcile api.v1 in a "
        f"lane:contract PR. Unaudited sealed gaps: {unaudited}"
    )


def test_no_stale_known_gaps():
    """A known-gap row for a route that IS now implemented is stale — delete it. Keeps the ledger honest."""
    implemented = _implemented_union()
    stale = sorted(r for r in _known_gap_set(_load_ledger()) if r in implemented)
    assert not stale, f"KNOWN_GAPS.json known_gaps list route(s) that are now implemented — remove them: {stale}"


def test_known_gaps_carry_reason_and_issue():
    """Every known-gap row states WHY it is deferred and links an issue (a bare waiver is undocumented drift)."""
    bad = [
        g for g in _load_ledger().get("known_gaps", [])
        if not (g.get("reason") or "").strip() or not (g.get("issue") or "").strip()
        or not {"method", "path"} <= g.keys()
    ]
    assert not bad, f"KNOWN_GAPS.json known_gaps row(s) missing method/path/reason/issue: {bad}"


def test_known_gaps_are_genuinely_declared_by_the_sealed_contract():
    """Guard the ledger's scope: a waived route must actually be DECLARED by api.v1 (so the ledger can't
    silence a route the contract doesn't have — a stale/typo'd waiver hiding nothing real)."""
    declared = _api_v1_declared()
    stray = sorted(r for r in _known_gap_set(_load_ledger()) if r not in declared)
    assert not stray, f"KNOWN_GAPS.json known_gaps name route(s) api.v1 does not declare (stale scope): {stray}"


# ── (c) golden-driven RESPONSE-SHAPE conformance: a field rename fails, not just a path removal ────
def _drive_bots_status() -> dict:
    """Drive the REAL meeting-api GET /bots/status handler (offline, in-memory) and return its body.
    Not the gateway's golden-replaying downstream — the SHIPPED collector, so a rename shows up."""
    from meeting_api.collector import create_app as collector_create_app
    from meeting_api.collector.fakes import InMemoryTranscriptStore

    class _CaptureRedis:
        async def publish(self, channel, data):  # noqa: D401 - port stub
            return None

    client = TestClient(collector_create_app(InMemoryTranscriptStore(), redis=_CaptureRedis()))
    resp = client.get("/bots/status", headers={"x-user-id": "7"})
    assert resp.status_code == 200, f"GET /bots/status → {resp.status_code}: {resp.text}"
    return resp.json()


# (golden file, drive-fn) — each golden whose top-level fields must be PRESENT in the real response.
_GOLDEN_SHAPE_CASES = [
    ("BotStatusResponse.example.json", _drive_bots_status),
]


@pytest.mark.parametrize("golden_name,drive", _GOLDEN_SHAPE_CASES, ids=[c[0] for c in _GOLDEN_SHAPE_CASES])
def test_golden_top_level_fields_present_in_real_response(golden_name, drive):
    """The frozen golden EXAMPLE drives the REAL response: every top-level key the golden declares must be
    PRESENT in the live body. Catches a sealed field being RENAMED (running_bots → running, #591 A2) — a
    live seal-shape violation the forward impl⊆contract check never saw."""
    golden = json.loads((_GOLDEN / golden_name).read_text(encoding="utf-8"))
    real = drive()
    missing = sorted(k for k in golden if k not in real)
    assert not missing, (
        f"{golden_name}: the real response is MISSING sealed golden field(s) {missing} — a sealed api.v1 "
        f"response field was renamed or dropped (real keys: {sorted(real)})"
    )
