"""gate:health — flows-api exposes a conforming liveness /health.

The gate discovers this file by name (`scripts/gates.mjs` gateHealth) for every Python package that
builds a FastAPI app, and a standing service without one is a RED rather than a green-on-empty: a
process nobody can probe is a process nobody can restart on evidence.

OFFLINE, and genuinely so (2026-09-03). `flows_api` builds its app at import and refuses to start
without its credentials — by design, so an unconfigured deployment stops at the door rather than
serving on a placeholder — so they are supplied here BEFORE the import, exactly as the rig's
conftest does. `VEXA_FLOWS_DB_URL` matters most, and it is a syntactically-real but UNREACHABLE
Postgres DSN (`127.0.0.1:1`, the same "never a service" convention the rig's conftest uses for its
offline doors) rather than an offline dialect: `flows.db_from_url` now refuses anything that is not
`postgres://`/`postgresql://` (SqliteDB — the old offline double — moved to a TEST fixture,
`sqlite_double.py`, and is never reachable through a URL any more), and `postgres_db` is LAZY — the
engine is created but does not connect, and the schema is applied on first real use, not at
construction. So this module composes and this gate passes with no database running anywhere: the
whole point of this file is that `flows-api can be built without a database` (gate:health / the
issue this file backs) is now true of the PRODUCTION adapter, not true because the test routed
around it onto a different one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

# SET, IMPORT, RESTORE — the env must not outlive this import.
# `flows_api` reads all three at module scope and keeps them as constants, so they are needed for
# exactly the duration of the import and are process-wide poison after it: a plain `setdefault`
# here made `tests/test_link_loop.py` fail, because it asserts on a secret it sets itself and
# whichever module imports FIRST wins an env var. Alphabetical order is not a contract.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        # UNREACHABLE on purpose (port 1 is never a service) — proves the import touches no
        # network. A real Postgres DSN shape is required: unset or `sqlite://` both refuse at
        # `db_from_url` now (Postgres is the only production dialect).
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://health-gate:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations.flows_api import app  # noqa: E402
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_health_ok():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "flows-api"
    # the registry's depth beside the status — flows-api reports what it has loaded, the way
    # meeting-api's receiver reports its store depth
    assert isinstance(body["flows"], int) and body["flows"] > 0
    assert isinstance(body["steps"], int) and body["steps"] > 0


def test_health_takes_no_credential():
    """The probe must not sit behind X-Flows-Operator-Key.

    Every other route on this surface takes a credential. If this one ever acquires one, an
    orchestrator without the operator key reads 401 — indistinguishable from a dead process to a
    restart policy, and one more place the key has to reach.
    """
    r = TestClient(app).get("/health", headers={})
    assert r.status_code == 200


def test_health_touches_no_database():
    """Liveness, not readiness: the route reads the in-memory registry and nothing else.

    A probe that dials the database reports the DATABASE, and an orchestrator then restarts a
    healthy process because a dependency blinked. `GET /reactions`, behind the key, is where the
    loop's own health is answered. Pinned by shape rather than by mocking: the handler's body is
    two `len()` calls over `vocab`, and this asserts the answer does not change when the DB is
    unreachable — which, since the module composed against an unreachable Postgres above, is
    already the state everything in this file is running in.

    Restores whatever `flows_api.db` WAS, not a freshly built one from this file's own URL:
    `flows_integrations.flows_api` is one module object, cached and shared across every test file
    that imports it in the same session (gate:test-isolation's whole concern) — another file may
    have swapped in a real, working double for its own tests, and rebuilding from THIS file's
    (deliberately unreachable) URL would clobber that for everyone running after this one.
    """
    from flows_integrations import flows_api

    first = TestClient(app).get("/health").json()
    saved_db = flows_api.db
    flows_api.db = None                      # the DB is gone; liveness must not notice
    try:
        assert TestClient(app).get("/health").json() == first
    finally:
        flows_api.db = saved_db
