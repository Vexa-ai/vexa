"""REAL-Postgres coverage for the #1064 agent-metadata filter (``GET /meetings?custom.<k>=<v>``).

The filter is a JSONB ``@>`` containment built in the PRODUCTION adapter, and two of its properties
exist only inside Postgres — no fake can witness them:

* the probe must bind as a jsonb OBJECT. SQLAlchemy's JSONB bind processor serializes the
  parameter, so a pre-serialized string binds as a jsonb STRING and ``object @> string`` is
  silently false for every row;
* Postgres' "an array contains its primitive elements" exception is TOP-LEVEL ONLY —
  ``{"tags":["sales","q3"]} @> {"tags":"sales"}`` is FALSE — which is why a stored list is matched
  by the adapter's second, array-shaped probe (``@> {"tags":["sales"]}``).

So everything below runs the shipped ``SqlAlchemyTranscriptStore.list_meetings`` against the real
schema in a real Postgres, gated on ``MEETING_API_TEST_DATABASE_URL`` (unset → clean skip).
"""
from __future__ import annotations

import pytest

from pg_fixtures import pg_schema, pg_store, requires_pg  # noqa: F401 (fixtures used by name)

USER_A = 4001
USER_B = 4002


async def _seed(session_factory, user_id: int, native_id: str, custom: dict) -> int:
    """Insert one meeting with ``data['custom'] = custom`` and return its id."""
    from meeting_api.collector.models import Meeting

    async with session_factory() as db:
        m = Meeting(
            user_id=user_id,
            platform="google_meet",
            platform_specific_id=native_id,
            status="completed",
            data={"custom": custom},
        )
        db.add(m)
        await db.commit()
        return m.id


async def _ids(store, user_id: int, **kw) -> list[int]:
    rows = await store.list_meetings(user_id, list_view=True, **kw)
    body = rows[0] if isinstance(rows, tuple) else rows
    return sorted(r["id"] for r in body)


@requires_pg
@pytest.mark.asyncio
async def test_numeric_filter_matches_stored_number(pg_store):
    """``{'score': 42}`` returns the meeting stored with the JSON number 42, and only that one."""
    store, sf = pg_store
    mid = await _seed(sf, USER_A, "num-1", {"score": 42})

    assert await _ids(store, USER_A) == [mid]                       # no filter → the row
    assert await _ids(store, USER_A, custom_filter={"score": 42}) == [mid]
    assert await _ids(store, USER_A, custom_filter={"score": 43}) == []


@requires_pg
@pytest.mark.asyncio
async def test_scalar_filter_is_type_strict(pg_store):
    """A string probe matches a stored string, never a stored number (``@>`` is type-strict)."""
    store, sf = pg_store
    as_str = await _seed(sf, USER_A, "str-1", {"priority": "3"})
    as_num = await _seed(sf, USER_A, "num-2", {"priority": 3})

    assert await _ids(store, USER_A, custom_filter={"priority": "3"}) == [as_str]
    assert await _ids(store, USER_A, custom_filter={"priority": 3}) == [as_num]


@requires_pg
@pytest.mark.asyncio
async def test_tag_filter_matches_array_membership(pg_store):
    """The headline agent case: a scalar probe against a stored LIST, via the array-shaped probe."""
    store, sf = pg_store
    sales = await _seed(sf, USER_A, "tags-1", {"tags": ["sales", "q3"]})
    await _seed(sf, USER_A, "tags-2", {"tags": ["q3"]})

    assert await _ids(store, USER_A, custom_filter={"tags": "sales"}) == [sales]
    assert await _ids(store, USER_A, custom_filter={"tags": "marketing"}) == []


@requires_pg
@pytest.mark.asyncio
async def test_absent_key_and_multi_key_and(pg_store):
    """A filter on a key nobody stored matches nothing; multiple keys are AND-ed."""
    store, sf = pg_store
    both = await _seed(sf, USER_A, "and-1", {"team": "sales", "score": 42})
    await _seed(sf, USER_A, "and-2", {"team": "sales", "score": 7})

    assert await _ids(store, USER_A, custom_filter={"missing": "x"}) == []
    assert await _ids(store, USER_A, custom_filter={"team": "sales", "score": 42}) == [both]
    assert await _ids(store, USER_A, custom_filter={"team": "sales", "score": 99}) == []


@requires_pg
@pytest.mark.asyncio
async def test_filter_never_crosses_tenants(pg_store):
    """User B's filter never returns user A's meetings, however well the value matches."""
    store, sf = pg_store
    a = await _seed(sf, USER_A, "x-1", {"team": "sales"})
    b = await _seed(sf, USER_B, "x-2", {"team": "sales"})

    assert await _ids(store, USER_A, custom_filter={"team": "sales"}) == [a]
    assert await _ids(store, USER_B, custom_filter={"team": "sales"}) == [b]
