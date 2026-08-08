"""The tenancy boundary, asserted against REAL Postgres instead of the in-memory fake (#1068).

``test_xtenant_isolation.py`` proves the cross-tenant contract at the ROUTE seam over
``InMemoryTranscriptStore``: correct, fast, offline — and blind to the SQL that actually enforces
the boundary in production. The fake's isolation is hand-written Python (``m["user_id"] ==
user_id``); the shipped isolation is a UNION of index-scannable access branches plus per-mutation
``WHERE user_id … FOR UPDATE`` re-selects, with any JSONB ``@>`` containment filter ANDed onto the
outer query. Those two are equal only by convention — nothing mechanical held them together, which
is the same failure class the #637 advisory-lock witness already cost us once (the fake never
executed the SQL, so a ``pg_try_advisory_lock(int4, bigint)`` mismatch reached a live meeting).

Every test here drives the PRODUCTION ``SqlAlchemyTranscriptStore`` against a real ``meetings``
table in a throwaway schema. Gated on ``MEETING_API_TEST_DATABASE_URL`` — see ``pg_fixtures.py``
for the fixture, its teardown guarantee, and the exact command that runs this lane.

Three consumers, one per gap named in #1068 / the #1067 review:
  1. cross-user isolation on the READ surface (list · get · get-by-id · authorize-subscribe),
  2. 404-when-not-owned across the owner-scoped MUTATION surface, plus proof no write landed,
  3. JSONB ``@>`` containment with TYPED values (number · bool · array · string) — the gap the
     #1067 filter fix could only cover at the SQL-construction layer and in a hand-written fake.
"""
from __future__ import annotations

import json

import pytest

# The fixtures live in their own module (imported, not conftest-registered) so this lane adds no
# shared-file surface. `requires_pg` is the same skipif shape test_single_flight.py established.
from pg_fixtures import pg_schema, pg_store, requires_pg  # noqa: F401  (pytest fixture injection)

USER_A = 4001
USER_B = 4002
PLATFORM = "google_meet"
SHARED_NATIVE = "abc-defg-hij"   # the SAME meeting link, run by BOTH users — the collision case
A_ONLY_NATIVE = "aaa-aaaa-aaa"   # a link only user A ever ran — the not-owned-at-all case


async def _seed_meeting(session_factory, *, user_id, native, status="completed", data=None,
                        segment_text=None):
    """INSERT one ``meetings`` row (+ optionally one ``transcriptions`` segment) via the ORM.

    Seeding through the models rather than through the adapter keeps the assertions honest: if a
    read path is broken, the seed is not broken in the same direction.
    """
    from meeting_api.sessions.models import Meeting, Transcription

    async with session_factory() as db:
        m = Meeting(user_id=user_id, platform=PLATFORM, platform_specific_id=native,
                    status=status, data=dict(data or {}))
        db.add(m)
        await db.flush()
        if segment_text is not None:
            db.add(Transcription(meeting_id=m.id, start_time=1.0, end_time=2.0,
                                 text=segment_text, speaker="Alice", language="en",
                                 segment_id=f"seg-{m.id}"))
        await db.commit()
        return m.id


async def _row_state(session_factory, meeting_id) -> dict:
    """Re-read the row straight from Postgres — the no-write-landed oracle."""
    from sqlalchemy import select

    from meeting_api.sessions.models import Meeting

    async with session_factory() as db:
        m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalars().first()
        return {"exists": m is not None,
                "status": None if m is None else m.status,
                "data": {} if m is None else dict(m.data or {})}


# ── 1 · cross-user isolation on the READ surface ────────────────────────────────────────────────

@requires_pg
async def test_cross_user_isolation_on_real_postgres(pg_store):
    """User B must never see user A's meeting — on ANY read path, against the real access UNION.

    Both users ran a bot on the SAME native link, so ``platform_specific_id`` alone cannot
    disambiguate them; only the ``user_id`` predicate can. The fake asserts this with a Python
    comparison. Here the three shipped branches (owner / transcript-share viewer / bound-workspace
    member) are compiled into an actual ``UNION ALL`` and executed, so an accidental ``OR``, a
    dropped ``user_id`` predicate, or a widened branch surfaces as a leak on one of these lines.
    """
    store, sf = pg_store
    a_id = await _seed_meeting(sf, user_id=USER_A, native=SHARED_NATIVE, segment_text="A secret")
    b_id = await _seed_meeting(sf, user_id=USER_B, native=SHARED_NATIVE, segment_text="B secret")
    a_solo = await _seed_meeting(sf, user_id=USER_A, native=A_ONLY_NATIVE, segment_text="A alone")
    assert len({a_id, b_id, a_solo}) == 3, "seed must produce three distinct rows"

    # LIST — each user sees only their own rows.
    a_list = await store.list_meetings(USER_A)
    b_list = await store.list_meetings(USER_B)
    assert {m["id"] for m in a_list} == {a_id, a_solo}
    assert {m["id"] for m in b_list} == {b_id}
    assert all(m["user_id"] == USER_A for m in a_list)
    assert all(m["user_id"] == USER_B for m in b_list)

    # LIST narrowed to the shared link's platform — the native-id collision does not merge tenants.
    assert {m["id"] for m in await store.list_meetings(USER_B, platform=PLATFORM)} == {b_id}
    # LIST addressed at a specific row id — the detail path is union-scoped too, not id-scoped.
    assert await store.list_meetings(USER_B, meeting_id=a_id) == []
    assert [m["id"] for m in await store.list_meetings(USER_A, meeting_id=a_id)] == [a_id]

    # GET by (user, platform, native) — the shared link resolves per-owner, never cross-tenant.
    a_doc = await store.get_transcript(USER_A, PLATFORM, SHARED_NATIVE)
    b_doc = await store.get_transcript(USER_B, PLATFORM, SHARED_NATIVE)
    assert a_doc["id"] == a_id and [s["text"] for s in a_doc["segments"]] == ["A secret"]
    assert b_doc["id"] == b_id and [s["text"] for s in b_doc["segments"]] == ["B secret"]
    assert await store.get_transcript(USER_B, PLATFORM, A_ONLY_NATIVE) is None

    # GET by ROW id — the P0 path. B addressing A's row id is refused (the route maps None → 404).
    assert await store.get_transcript_by_id(USER_B, a_id) is None, "B read A's row by id — LEAK"
    assert await store.get_transcript_by_id(USER_B, a_solo) is None
    assert (await store.get_transcript_by_id(USER_A, a_id))["id"] == a_id

    # AUTHORIZE-SUBSCRIBE — the live-feed boundary; B resolves to B's row, never A's.
    assert await store.authorize_subscribe(USER_B, PLATFORM, SHARED_NATIVE) == b_id
    assert await store.authorize_subscribe(USER_A, PLATFORM, SHARED_NATIVE) == a_id
    assert await store.authorize_subscribe(USER_B, PLATFORM, A_ONLY_NATIVE) is None

    # The union's SHARE branch is itself a real JSONB `@>` containment (`to_jsonb(<int>)` probed
    # against `data.transcript_viewers`). It must WIDEN for a genuine viewer …
    shared_id = await _seed_meeting(sf, user_id=USER_A, native="shr-eeee-fff",
                                    data={"transcript_viewers": [USER_B]})
    b_after_share = await store.list_meetings(USER_B)
    assert {m["id"] for m in b_after_share} == {b_id, shared_id}
    assert next(m for m in b_after_share if m["id"] == shared_id)["shared"] is True
    assert (await store.get_transcript_by_id(USER_B, shared_id))["id"] == shared_id

    # … and must NOT widen on a type-confused entry. `to_jsonb(4002)` is the JSON NUMBER 4002 and
    # Postgres `@>` is type-exact, so a stored STRING "4002" grants nothing. Same typed-value
    # semantics the custom-metadata filter rides on, proven here on the branch that ships today.
    string_viewer = await _seed_meeting(sf, user_id=USER_A, native="str-eeee-fff",
                                        data={"transcript_viewers": [str(USER_B)]})
    assert string_viewer not in {m["id"] for m in await store.list_meetings(USER_B)}, \
        'a string "4002" viewer entry must not authorize the integer user 4002'


# ── 2 · 404-when-not-owned across the owner-scoped MUTATION surface ─────────────────────────────

@requires_pg
async def test_owner_scoped_mutations_404_when_not_owned(pg_store):
    """Every owner-scoped write refuses a non-owner with ``None`` (→ 404) and leaves NO trace.

    Each method re-selects ``WHERE user_id AND platform AND platform_specific_id … FOR UPDATE``
    (or ``WHERE id AND user_id`` for the row-addressed ones) before it mutates — the exact shape
    ``merge_custom_metadata`` uses for the #1064 metadata PATCH. The fake returns ``None`` from a
    Python guard; here the refusal has to come from the SQL, and each row is read back afterwards
    to prove the write did not land through some other path.

    ``A_ONLY_NATIVE`` is deliberately a link user B has NEVER run: against a shared native, a
    passing call could be B legitimately mutating B's own row and the assertion would prove nothing.
    """
    store, sf = pg_store
    a_id = await _seed_meeting(sf, user_id=USER_A, native=A_ONLY_NATIVE, status="completed",
                               data={"constructed_meeting_url": "https://meet/aaa"})
    a_planned = await _seed_meeting(sf, user_id=USER_A, native="pln-aaaa-aaa", status="scheduled",
                                    data={"title": "A's plan"})
    before, before_planned = await _row_state(sf, a_id), await _row_state(sf, a_planned)

    # native-addressed owner-scoped writes
    assert await store.bind_workspace(USER_B, PLATFORM, A_ONLY_NATIVE, "ws-of-b") is None
    assert await store.connect_doc(USER_B, PLATFORM, A_ONLY_NATIVE,
                                   {"workspace": "ws-of-b", "path": "/pwn.md"}) is None
    assert await store.disconnect_doc(USER_B, PLATFORM, A_ONLY_NATIVE, "/pwn.md") is None
    assert await store.set_intent(USER_B, PLATFORM, A_ONLY_NATIVE, "idle") is None
    assert await store.mint_transcript_share(USER_B, PLATFORM, A_ONLY_NATIVE) is None

    # row-id-addressed owner-scoped writes (the shape the metadata PATCH shares)
    assert await store.update_planned_meeting(USER_B, a_planned, {"title": "pwned"}) is None
    assert await store.delete_planned_meeting(USER_B, a_planned) is None

    # NOTHING landed: both rows byte-identical, and the planned row still exists.
    assert await _row_state(sf, a_id) == before, "a refused mutation still wrote to A's row"
    assert await _row_state(sf, a_planned) == before_planned
    assert before_planned["exists"] and before_planned["status"] == "scheduled"

    # Positive control — the SAME calls succeed for the owner, so the refusals above are the
    # `user_id` predicate doing its job, not the methods being inert.
    assert await store.bind_workspace(USER_A, PLATFORM, A_ONLY_NATIVE, "ws-of-a") == "ws-of-a"
    assert await store.connect_doc(USER_A, PLATFORM, A_ONLY_NATIVE,
                                   {"workspace": "ws-of-a", "path": "/notes.md"}) == [
        {"workspace": "ws-of-a", "path": "/notes.md"}]
    assert (await store.update_planned_meeting(
        USER_A, a_planned, {"title": "kept"}))["data"]["title"] == "kept"
    assert await store.delete_planned_meeting(USER_A, a_planned) is True
    assert (await _row_state(sf, a_planned))["exists"] is False

    # …and B still cannot read what A just wrote.
    assert await store.get_transcript_by_id(USER_B, a_id) is None
    assert (await _row_state(sf, a_id))["data"]["workspace_id"] == "ws-of-a"


# ── 3 · JSONB `@>` containment with TYPED values ────────────────────────────────────────────────
#
# #1067 fixed the wire→JSON coercion for `?custom.*` filters ("42" must become the NUMBER 42 before
# it reaches `@>`, or a numeric attribute is unfilterable). With no table-creating fixture in this
# suite, that fix could only be asserted at the SQL-CONSTRUCTION layer plus a hand-written fake —
# nothing ever executed `@>` against Postgres, so "the fake mirrors JSONB containment" stayed an
# assumption. These two tests are the missing oracle.

def _custom_contains(needle: dict):
    """The whole-column containment predicate the metadata filter compiles to.

    ``data @> {"custom": …}`` (not ``data->'custom' @> …``) so the existing whole-column GIN index
    ``ix_meeting_data_gin`` can serve it. The probe is bound as a Python OBJECT and left for
    SQLAlchemy's JSONB type to serialize — see the double-encoding guard below for why that detail
    is load-bearing.
    """
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    from meeting_api.sessions.models import Meeting

    return Meeting.data.op("@>")(cast({"custom": needle}, JSONB))


CUSTOM = {"score": 42, "flagged": True, "tags": ["red", "blue"], "owner": "ada"}


@requires_pg
@pytest.mark.parametrize(
    "probe,should_match",
    [
        ({"score": 42}, True),                     # number ≡ number
        ({"score": "42"}, False),                  # number ≢ the string "42" — `@>` is type-exact
        ({"score": 42.0}, True),                   # 42 and 42.0 are the same jsonb numeric
        ({"flagged": True}, True),                 # bool ≡ bool
        ({"flagged": "true"}, False),              # bool ≢ the string "true"
        ({"flagged": False}, False),               # the OTHER bool matches nothing
        ({"tags": ["red"]}, True),                 # array containment is ⊆, not equality
        ({"tags": ["blue", "red"]}, True),         # …and order-insensitive
        ({"tags": ["green"]}, False),
        # NESTED scalar-vs-array does NOT match. Postgres' "an array may contain a primitive"
        # exception applies ONLY at the top level of the comparison, never inside an object — so a
        # tag filter must probe with a one-element ARRAY, not a bare scalar.
        ({"tags": "red"}, False),
        ({"owner": "ada"}, True),                  # string ≡ string (what coercion must not break)
        ({"owner": "ADA"}, False),                 # …and it is case-sensitive
        ({"score": 42, "flagged": True}, True),    # multi-key: ALL pairs must be contained
        ({"score": 42, "flagged": False}, False),
        ({"missing": 1}, False),                   # an absent key contains nothing
    ],
)
async def test_jsonb_containment_typed_values_on_real_postgres(pg_store, probe, should_match):
    """Real-Postgres ``@>`` semantics for numeric / bool / array / string metadata values.

    Each row of the matrix is a coercion rule the request boundary has to honour, checked against
    the database rather than against a Python re-implementation of it. The probe is ALSO run for a
    second user holding byte-identical ``data.custom``: a filter is only ever ANDed onto the access
    union and must never widen it.
    """
    from sqlalchemy import select

    from meeting_api.sessions.models import Meeting

    # The adapter is not called here on purpose: `list_meetings` grows its `custom_filter=` argument
    # with #1064/#1067, which is not on main yet. What IS pinned is the predicate that filter
    # compiles to, executed on the real engine (see gap 1 in the PR body).
    _store, sf = pg_store
    a_id = await _seed_meeting(sf, user_id=USER_A, native=SHARED_NATIVE, data={"custom": CUSTOM})
    b_id = await _seed_meeting(sf, user_id=USER_B, native=SHARED_NATIVE, data={"custom": CUSTOM})

    async with sf() as db:
        def _q(user_id):
            return select(Meeting.id).where(Meeting.user_id == user_id, _custom_contains(probe))
        a_hits = set((await db.execute(_q(USER_A))).scalars().all())
        b_hits = set((await db.execute(_q(USER_B))).scalars().all())

    assert a_hits == ({a_id} if should_match else set()), (
        f"real-PG `@>` disagreed for {probe!r}: expected match={should_match}, got {a_hits}")
    # User scoping is what keeps a matching filter from becoming a cross-tenant read: B's identical
    # payload matches for B and ONLY for B.
    assert b_hits == ({b_id} if should_match else set())
    assert a_id not in b_hits and b_id not in a_hits, "containment filter leaked across users"


@requires_pg
async def test_containment_probe_must_be_a_jsonb_object_not_a_double_encoded_string(pg_store):
    """The construction trap the fake could not see: ``cast(json.dumps(obj), JSONB)`` matches NOTHING.

    ``JSONB``'s bind processor serializes whatever Python value it is handed. Hand it a dict and the
    parameter arrives as the jsonb OBJECT the filter needs; hand it an ALREADY-serialized string and
    it is serialized a second time, so the parameter arrives as a jsonb STRING SCALAR
    (``'"{\\"custom\\": …}"'``). ``object @> string-scalar`` is simply false — no error, no warning,
    just a filter that silently returns zero rows for every query.

    An in-memory fake that re-implements containment in Python cannot expose this: it never builds
    the bind parameter. That is the whole argument for this file.
    """
    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import JSONB

    from meeting_api.sessions.models import Meeting

    _store, sf = pg_store
    a_id = await _seed_meeting(sf, user_id=USER_A, native=SHARED_NATIVE, data={"custom": CUSTOM})
    needle = {"custom": {"score": 42}}

    async with sf() as db:
        # The probe parameter itself: a dict binds as an object, a pre-serialized string does not.
        assert isinstance((await db.execute(select(cast(needle, JSONB)))).scalar(), dict)
        double = (await db.execute(select(cast(json.dumps(needle), JSONB)))).scalar()
        assert isinstance(double, str), (
            "cast(json.dumps(obj), JSONB) no longer double-encodes — the trap this test guards is "
            "gone and the guard can be relaxed")

        good = select(Meeting.id).where(_custom_contains({"score": 42}))
        bad = select(Meeting.id).where(Meeting.data.op("@>")(cast(json.dumps(needle), JSONB)))
        assert (await db.execute(good)).scalars().all() == [a_id]
        assert (await db.execute(bad)).scalars().all() == [], (
            "a double-encoded probe matched a row — this test's premise is wrong")
