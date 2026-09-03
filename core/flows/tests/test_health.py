"""gate:health — flows-api exposes a conforming liveness /health.

The gate discovers this file by name (`scripts/gates.mjs` gateHealth) for every Python package that
builds a FastAPI app, and a standing service without one is a RED rather than a green-on-empty: a
process nobody can probe is a process nobody can restart on evidence.

OFFLINE. `flows_api` builds its app at import and refuses to start without its credentials — by
design, so an unconfigured deployment stops at the door rather than serving on a placeholder — so
they are supplied here BEFORE the import, exactly as the rig's conftest does. `VEXA_FLOWS_DB_URL`
matters most, and for two reasons: unset, `db_url()` shells out to `docker exec` to read a password
off a running container; and `postgres_db` APPLIES THE SCHEMA at construction, so the module cannot
be imported at all against a Postgres that is not there. `flows.db_from_url` reads the dialect off
the URL, so `sqlite://` composes the same app on the in-memory dialect the whole offline suite
already runs on.
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
        # the offline dialect (flows.db_from_url) — unset, db_url() shells out to `docker exec`
        "VEXA_FLOWS_DB_URL": "sqlite://"}
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
    """The probe must not sit behind X-Flows-Admin-Key.

    Every other route on this surface carries `Depends(auth)`. If this one ever acquires it, an
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
    unreachable — which is what the sqlite composition above already is.
    """
    from flows_integrations import flows_api

    first = TestClient(app).get("/health").json()
    flows_api.db = None                      # the DB is gone; liveness must not notice
    try:
        assert TestClient(app).get("/health").json() == first
    finally:
        flows_api.db = flows_api.db_from_url(_ENV["VEXA_FLOWS_DB_URL"])
