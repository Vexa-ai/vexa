"""The friction dedup upsert on REAL Postgres — skipped unless `FLOWS_TEST_PG_URL` names one.

`test_friction_dedup.py` runs the whole behaviour on the sqlite double. This file proves the one
statement whose correctness is dialect-specific — `INSERT … ON CONFLICT (dedup_key) DO UPDATE …
RETURNING` — on the production dialect, through the production adapter (`flows.db.postgres_db`),
and proves the property the upsert exists for: concurrent first reports admit exactly ONE reaction.

Run it against a throwaway database, never a shared one; it creates the flows schema there:

    FLOWS_TEST_PG_URL=postgresql+pg8000://postgres:pw@127.0.0.1:55432/postgres \
        uv run pytest -q tests/test_friction_dedup_postgres.py

Each test files under its own random uid and reads back only that uid's rows, so it never needs to
delete anything and never collides with another run.
"""
from __future__ import annotations

import os
import threading
import uuid

import pytest

PG_URL = os.environ.get("FLOWS_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="FLOWS_TEST_PG_URL not set — no real Postgres")

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-friction",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://friction:unreachable@127.0.0.1:1/flows"}

REFUSAL = '{"status": "refused", "reason": "human_session_required"}'


@pytest.fixture(scope="module")
def pg():
    from flows.db import postgres_db
    return postgres_db(PG_URL)


@pytest.fixture()
def api(pg):
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    saved_db = flows_api.db
    flows_api.db = pg
    try:
        yield flows_api, TestClient
    finally:
        flows_api.db = saved_db


def _post(flows_api, client_cls, uid, **kw):
    return client_cls(flows_api.app).post(
        "/friction", json=kw,
        headers={"X-Flows-Operator-Key": flows_api.API_KEY, "X-User-Id": uid})


def _reactions_for(pg, uid) -> int:
    rows = pg.execute("SELECT subject_refs FROM reaction WHERE event_type = 'friction.reported'")
    import json
    return sum(1 for (refs,) in rows if json.loads(refs).get("uid") == uid)


def test_the_upsert_counts_on_postgres(pg):
    from flows_integrations import friction_dedup

    key = uuid.uuid4().hex
    fid = f"fr_pg{key[:8]}"
    assert friction_dedup.record_occurrence(pg, key=key, friction_id=fid, uid="u",
                                            now=1.0) == (fid, 1)
    pg.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        created_at, updated_at)
                  VALUES (:rid, :sid, 'friction.reported', '{}', 'friction_log', 1,
                          'record_friction', 'done', 0, 0, 1, 1)""",
               {"rid": uuid.uuid4().hex, "sid": f"friction-{fid}::friction_log"})
    assert friction_dedup.record_occurrence(pg, key=key, friction_id="fr_other", uid="u",
                                            now=2.0) == (fid, 2)
    rows = pg.execute("SELECT occurrences, first_seen, last_seen FROM friction_occurrence "
                      "WHERE dedup_key = :k", {"k": key})
    assert [(int(x), float(y), float(z)) for x, y, z in rows] == [(2, 1.0, 2.0)]


def test_ten_identical_reports_through_the_route_are_one_reaction_on_postgres(api, pg):
    flows_api, client_cls = api
    uid = f"pg-{uuid.uuid4().hex[:10]}"
    answers = [_post(flows_api, client_cls, uid, tool="gmail_search",
                     what_happened=REFUSAL).json() for _ in range(10)]
    assert answers[0]["recorded"] is True
    assert [a["occurrences"] for a in answers[1:]] == list(range(2, 11))
    assert all(a["duplicate_of"] == answers[0]["id"] for a in answers[1:])
    assert _reactions_for(pg, uid) == 1


def test_simultaneous_first_reports_admit_exactly_one_reaction_on_postgres(api, pg):
    """Eight threads file the same first report at the same moment. The upsert is the only thing
    that decides first-vs-repeat, so exactly one of them is admitted and the rest are counted."""
    flows_api, client_cls = api
    uid = f"pg-{uuid.uuid4().hex[:10]}"
    n = 8
    barrier = threading.Barrier(n)
    answers: list = [None] * n

    def one(i):
        client = client_cls(flows_api.app)
        barrier.wait()
        answers[i] = client.post(
            "/friction", json={"tool": "gmail_search", "what_happened": REFUSAL},
            headers={"X-Flows-Operator-Key": flows_api.API_KEY, "X-User-Id": uid}).json()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    firsts = [a for a in answers if a["recorded"] is True]
    repeats = [a for a in answers if a["recorded"] is False]
    assert len(firsts) == 1, answers
    assert len(repeats) == n - 1
    assert {a["duplicate_of"] for a in repeats} == {firsts[0]["id"]}
    assert sorted(a["occurrences"] for a in repeats) == list(range(2, n + 1))
    assert _reactions_for(pg, uid) == 1


def test_a_different_uid_is_a_separate_row_on_postgres(api, pg):
    flows_api, client_cls = api
    a, b = f"pg-{uuid.uuid4().hex[:10]}", f"pg-{uuid.uuid4().hex[:10]}"
    ra = _post(flows_api, client_cls, a, tool="t", what_happened=REFUSAL).json()
    rb = _post(flows_api, client_cls, b, tool="t", what_happened=REFUSAL).json()
    assert ra["recorded"] is True and rb["recorded"] is True and ra["id"] != rb["id"]
