"""Ports (Protocols) — the seams that let the SAME ``create_app`` / ``ingest`` run with real
adapters in production and injected fakes in tests.

The deployed transcription-collector (``services/meeting-api/meeting_api/collector/``) talks to
two collaborators:

  * **Postgres / Redis** as the transcript store — the meeting record (``meeting.data`` JSONB +
    transcript segments) is read for ``GET /transcripts`` / ``GET /meetings`` and authorized for
    ``POST /ws/authorize-subscribe`` (``collector/endpoints.py``); the segment-ingestion worker
    appends new segments (``collector/processors.py``).
  * **Redis** as the bus — the worker XREADGROUPs the ``transcription_segments`` stream
    (``collector/consumer.py``) and PUBLISHes change-only updates to
    ``tc:meeting:{id}:mutable`` (``services/redis.md`` — the pubsub the gateway ``/ws`` fans in).

Each collaborator is a ``typing.Protocol`` so the app depends on BEHAVIOR, not a concrete client.
``adapters.py`` supplies the production implementations (SQLAlchemy/redis-asyncio); the eval +
conformance harness supply in-process fakes (an in-memory store + fakeredis). Both satisfy these
Protocols structurally — no inheritance required.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


@runtime_checkable
class TranscriptStore(Protocol):
    """Read a meeting's transcript; list a user's meetings; append a segment; authorize a
    subscribe. Mirrors the SQL the deployed ``collector/endpoints.py`` runs against the
    ``meetings`` / ``transcriptions`` tables (``meeting.data`` JSONB is the recordings/notes
    home — there is NO separate recordings table)."""

    async def get_transcript(
        self, user_id: int, platform: str, native_meeting_id: str
    ) -> Optional[dict]:
        """The transcript document for ``(user, platform, native_id)`` — an api.v1
        ``TranscriptionResponse``-shaped dict (id, platform, status, start/end, segments[], …),
        or ``None`` when the user owns no such meeting (the route maps ``None`` → 404)."""
        ...

    async def get_transcript_by_id(
        self, user_id: int, meeting_id: int, member_workspaces: "Optional[set[str]]" = None
    ) -> Optional[dict]:
        """The transcript document for a SPECIFIC meeting ROW (``meeting.id``), authorized by owner OR
        transcript-share viewer OR bound-workspace member (``member_workspaces``) — the same api.v1
        ``TranscriptionResponse`` shape ``get_transcript`` returns, or ``None`` when unauthorized.

        P0 (wrong-row hydration fix): ``get_transcript`` resolves ``(user, platform, native_id)`` to
        the NEWEST matching row, so a user with several rows on the same native link always reads the
        latest — the terminal can't address an OLDER row's notes. This by-ROW-id path lets the
        terminal fetch EXACTLY the row it is displaying (each row is a distinct meeting run). Still
        owner-scoped: a row owned by another user returns ``None`` (404), never another tenant's data."""
        ...

    async def list_meetings(
        self,
        user_id: int,
        *,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        member_workspaces: "Optional[set[str]]" = None,
        metadata_filter: "Optional[dict]" = None,
    ) -> list[dict]:
        """The user's meetings, newest first — a list of api.v1 ``MeetingResponse``-shaped dicts
        (the body of ``MeetingListResponse``).

        ``metadata_filter`` selects rows whose ``data.metadata`` CONTAINS the given object (JSONB
        ``@>``, served by ``ix_meeting_data_gin``). It is what turns the list from a log into a
        queryable store: an agent that stamped ``{"crm_deal": "acme-42"}`` onto its meetings can
        ask for exactly those back instead of paging the account and filtering client-side."""
        ...

    async def authorize_subscribe(
        self, user_id: int, platform: str, native_meeting_id: str,
        member_workspaces: "Optional[set[str]]" = None,
    ) -> Optional[int]:
        """Resolve ``(user, platform, native_id)`` → the internal ``meeting_id`` the caller may subscribe
        to, or ``None``. Two branches: OWNERSHIP (the caller owns the meeting) OR MEMBERSHIP (the meeting is
        bound — ``data.workspace_id`` — to a shared workspace in ``member_workspaces``, the caller's set).
        The authorization boundary (``ws_authorize_subscribe``)."""
        ...

    async def bind_workspace(
        self, user_id: int, platform: str, native_meeting_id: str, workspace_id: str
    ) -> "Optional[str]":
        """OWNER-scoped: bind the meeting to a shared workspace (``data.workspace_id``) so its members can
        subscribe to the live feed. Returns the bound id, or ``None`` when the user owns no such meeting."""
        ...

    async def mint_transcript_share(
        self, user_id: int, platform: str, native_meeting_id: str, *,
        mode: str = "open", allowed_emails: "Optional[list]" = None, expires_in_sec: int = 86400,
    ) -> "Optional[dict]":
        """OWNER-scoped: mint an INDEPENDENT transcript share grant (``data.share_grants[]``, hash-at-rest).
        Returns {id, token, ...} once, or ``None`` when the user owns no such meeting."""
        ...

    async def redeem_transcript_share(
        self, user_id: int, user_email: "Optional[str]", token: str
    ) -> "Optional[dict]":
        """Redeem a transcript share token (any authed user) → adds them to ``data.transcript_viewers[]``.
        Returns {meeting_id, ok}, {error}, or ``None`` (malformed/unknown token). The token IS the authz."""
        ...

    async def get_meeting_participants(
        self, user_id: int, platform: str, native_meeting_id: str
    ) -> Optional[dict]:
        """OWNER-scoped read of everything 0.12 actually PERSISTS about who was in a meeting.

        Returns ``{"meeting_id": int, "invited": [{email, name?, response_status?}], "speakers": [str]}``,
        or ``None`` when the caller owns no such meeting (the route maps ``None`` → 404 — never an
        empty roster, which would leak "this meeting exists but is empty" across a tenant boundary).

        Two sources, and they are NOT the same thing:

        * ``invited`` — ``meeting.data['attendees']``, the ATTENDEE lines of the calendar invitation
          the meeting was imported from (``calendar_sync.service._attendees``). Real people, silent
          ones included — but only for meetings that came in through a connected calendar feed, and
          it records who was ASKED, not who showed up.
        * ``speakers`` — ``SELECT DISTINCT transcriptions.speaker``, first-heard first. People who
          were heard AND whose name resolved. Not an attendance list: a participant who never spoke,
          or whose platform tile never yielded a name, is absent by construction.

        There is deliberately NO third source, because the 0.12 core captures none: the platform
        modules observe only tiles that emit a SPEAKING signal, and no producer writes an observed
        roster to any store (Vexa-ai/vexa#861). This port must never synthesize one from the two
        above — inventing attendance from speech is the exact failure #861's preparation forbids."""
        ...

    async def append_segment(self, meeting_id: int, segment: dict) -> None:
        """Persist one ingested transcript segment for ``meeting_id`` (keyed by its
        ``segment_id`` — stable identity, last-write-wins, exactly the collector's Redis-hash
        persistence)."""
        ...

    async def delete_segments(self, meeting_id: int, segment_ids: list) -> None:
        """Withdraw retracted drafts by ``segment_id``: drop them from the live segments hash (before an
        un-flushed draft reaches Postgres) AND delete any already-flushed rows. Idempotent — a missing id
        is a no-op. The mixed lane's full-replace pending tail leaves stale drafts otherwise."""
        ...

    async def connect_doc(
        self, user_id: int, platform: str, native_meeting_id: str, doc: dict
    ) -> Optional[list[dict]]:
        """Append a workspace-doc ref ``{workspace, path, title?, kind?}`` to the owned meeting's
        ``meeting.data['docs']`` (created if absent), deduped by ``path`` (idempotent — re-connecting
        the same path updates in place). Returns the updated ``docs`` list, or ``None`` when the user
        owns no such meeting (the route maps ``None`` → 404). Doc BODIES live in the agent workspace;
        only refs land here."""
        ...

    async def disconnect_doc(
        self, user_id: int, platform: str, native_meeting_id: str, path: str
    ) -> Optional[list[dict]]:
        """Remove the doc ref with ``path`` from the owned meeting's ``meeting.data['docs']``.
        Returns the updated ``docs`` list (idempotent if absent), or ``None`` when not owned/found."""
        ...

    async def set_intent(
        self,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        status: str,
        scheduled_at: Optional[str] = None,
    ) -> Optional[dict]:
        """Write an INTENT status (``idle`` / ``scheduled`` ONLY) onto the owned meeting's
        ``meetings.status`` column — the user is the source of truth for these pre-FSM states.
        For ``scheduled`` the ISO8601 ``scheduled_at`` is stamped into ``meeting.data``; for
        ``idle`` it is cleared. NEVER reaches the bot FSM / ``LifecycleSink.apply_change``.

        Returns a small dict ``{id, user_id, platform, native_id, status, scheduled_at, changed}``
        describing the row after the write (``changed`` is False when the status was already the
        requested value AND scheduled_at is unchanged — an idempotent no-op that must NOT re-publish),
        or ``None`` when the user owns no such meeting."""
        ...

    async def create_planned_meeting(
        self,
        user_id: int,
        *,
        platform: str,
        native_meeting_id: Optional[str],
        title: Optional[str] = None,
        scheduled_at: Optional[str] = None,
        meeting_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        auto_join: bool = True,
        calendar_uid: Optional[str] = None,
        calendar_source: Optional[dict] = None,
        workspace_source: Optional[str] = None,
        attendees: Optional[list] = None,
        auto_join_last_attempt: Optional[str] = None,
        auto_join_error: Optional[str] = None,
    ) -> dict:
        """Create a PLANNED meeting row — status ``scheduled`` (when ``scheduled_at`` is set) or
        ``idle`` — with NO bot spawned. Link-less plans use ``platform='unknown'`` +
        ``native_meeting_id=None`` (mutations then address the ROW id). ``title`` /
        ``scheduled_at`` / ``workspace_id`` / ``auto_join`` land in ``meeting.data``.

        Serializes with concurrent spawns via the same per-user advisory lock
        ``create_meeting_guarded`` takes. Returns the created row (``list_meetings`` shape), or
        ``{"error": "duplicate"}`` when a NON-TERMINAL row already exists for
        ``(user, platform, native)`` (the route maps it → 409).

        ``auto_join_last_attempt`` / ``auto_join_error`` seed the row with an auto-join backoff
        earned elsewhere. Calendar sync passes them when this row replaces a TERMINAL row the
        auto-join sweep already dispatched for, so a re-imported occurrence is not due the instant
        it exists. They must land in the INSERT, never in a follow-up patch: a sweep tick between
        the two would spawn."""
        ...

    async def update_planned_meeting(
        self, user_id: int, meeting_id: int, updates: dict
    ) -> Optional[dict]:
        """OWNER-scoped, ROW-id-addressed edit of a PLANNED meeting. Refused unless the row's
        status is an intent status (``idle``/``scheduled``) — the bot FSM is never fought.

        ``updates`` carries only the keys the caller sent (PATCH semantics): ``title`` (None
        clears), ``scheduled_at`` (ISO8601; None clears → status flips to ``idle``; a value flips
        to ``scheduled``), ``platform``+``native_meeting_id``+``constructed_meeting_url`` (from a
        parsed ``meeting_url``), ``workspace_id`` (None unbinds), ``auto_join`` (bool).

        Returns the updated row (``list_meetings`` shape), ``None`` when the user owns no such
        row (→ 404), ``{"error": "conflict"}`` when the row advanced into the FSM (→ 409), or
        ``{"error": "duplicate"}`` when a new native id collides with another non-terminal row."""
        ...

    async def attach_calendar_source(
        self, user_id: int, meeting_id: int, *, calendar_uid: str,
        calendar_sources: Optional[list] = None,
    ) -> Optional[dict]:
        """Stamp calendar IDENTITY onto a row in ANY status — including one the bot FSM owns.

        The narrow complement of ``update_planned_meeting``: calendar sync uses it when an imported
        event's meeting is ALREADY LIVE, so the live row carries the calendar's uid/sources instead
        of a duplicate planned row being created beside it (which the auto-join sweep would then
        dispatch a SECOND bot for). Writes ONLY ``calendar_uid`` / ``calendar_sources`` and the
        singular ``calendar_connection_id`` / ``calendar_name`` mirrors — never ``auto_join``,
        ``auto_join_user_set``, ``calendar_managed``, ``scheduled_at`` or ``status``.

        Returns the row (``list_meetings`` shape), or ``None`` when the user owns no such row."""
        ...

    async def search_transcripts(
        self,
        user_id: int,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        platform: Optional[str] = None,
        native_meeting_id: Optional[str] = None,
    ) -> list[dict]:
        """Full-text search over the CALLER'S OWN transcript segments, ranked, with snippets.

        Answers the question metadata cannot: not "meetings I tagged X" but "meetings where
        someone SAID X". Without it the only way to answer that is to pull every transcript and
        read it — precisely the thing an agent must not do.

        Lexical, not semantic: Postgres FTS over ``to_tsvector('english', text)``. The query is
        parsed with ``websearch_to_tsquery`` so a caller gets quoted phrases, ``or`` and ``-term``
        negation for free, and — unlike ``to_tsquery`` — malformed input never raises. Ranked by
        ``ts_rank_cd`` (cover density: term frequency weighted by proximity; NOT BM25 — no IDF, no
        length normalisation). Each hit carries a ``ts_headline`` snippet rather than the whole
        segment, which is what keeps a result cheap in a calling model's context.

        ``'english'`` is the text-search config for BOTH indexing and querying, and it must stay
        the same on both sides or the index goes unused. It is not an English-only decision:
        English stemming leaves unknown tokens (e.g. Cyrillic) untouched, so non-English terms
        still match EXACTLY — what is lost is stemming for those languages, while ``'simple'``
        would have thrown away English stemming for everyone.

        OWNER-SCOPED ONLY — fail-closed, and deliberately narrower than ``list_meetings``, which
        also surfaces share-recipient and workspace-member rows. Widening search to those is a
        separate decision with its own review; a search that over-returns is a disclosure.

        Each hit: ``meeting_id`` · ``platform`` · ``native_meeting_id`` · ``start``/``end`` ·
        ``speaker`` · ``language`` · ``rank`` · ``snippet`` · ``text``. Newest-meeting-first
        within equal rank so a tie is deterministic."""
        ...

    async def annotate_meeting(
        self, user_id: int, meeting_id: int, *,
        title: Optional[str] = None,
        metadata: "Optional[dict]" = None,
    ) -> Optional[dict]:
        """Attach the CALLER's own annotations to a row in ANY status — including one the bot FSM
        owns, and including one already completed.

        The sibling of ``attach_calendar_source`` (identity stamped onto a live row) rather than of
        ``update_planned_meeting`` (which refuses an FSM row, because dispatch parameters must not
        change under a running bot). The distinction is what is being written, not when:
        ``update_planned_meeting`` edits the INSTRUCTIONS for a meeting — url, schedule, auto-join
        — and changing those mid-flight fights the FSM. ``title`` and ``metadata`` are the caller's
        DESCRIPTION of a meeting. Nothing in the pipeline reads them, so writing them can never
        re-arm, re-dispatch or re-route anything — and the moments a description is most worth
        writing are exactly the ones the FSM owns: mid-meeting, and after it ends.

        ``metadata`` is arbitrary caller-owned JSON stored at ``data.metadata``. It ALWAYS merges
        key-wise; a key set to ``None`` deletes that one key. There is deliberately NO whole-object
        replace: every writer shares one API key, so a replace would let a caller destroy keys
        written by another agent — or by the human — that it never saw. Merge plus explicit nulls
        expresses every legitimate edit while making it impossible to affect a key you did not
        name. It is the join key between a Vexa meeting and everything else the caller knows —
        a CRM record, a ticket, its own summary — and it is queryable through
        ``list_meetings(metadata_filter=...)``.

        Returns the updated row (``list_meetings`` shape), or ``None`` when the user owns no such
        row (→ 404). Never returns a conflict: there is no state in which annotating is refused."""
        ...

    async def delete_planned_meeting(self, user_id: int, meeting_id: int) -> Optional[bool]:
        """OWNER-scoped delete of a PLANNED (``idle``/``scheduled``) row. Returns ``True`` on
        delete, ``None`` when the user owns no such row (→ 404), ``False`` when the row is
        FSM-owned (→ 409). An FSM row is never deletable from here."""
        ...

    async def prepare_completed_artifact_deletion(
        self, user_id: int, meeting_id: int
    ) -> Optional[dict]:
        """OWNER-scoped snapshot for a completed/failed meeting's artifact erasure.

        Returns ``None`` for unknown/unowned, ``{"error": "conflict"}`` while the FSM is active,
        or a retryable snapshot containing the persisted recording metadata. It records a
        ``pending`` tombstone under the row lock so ``continue_meeting`` cannot reopen the row, but
        retains every artifact and path; storage failures therefore remain addressable/retryable.
        """
        ...

    async def finalize_completed_artifact_deletion(
        self, user_id: int, meeting_id: int
    ) -> Optional[bool]:
        """After object deletion succeeds, erase transcript rows and recording metadata atomically.

        The terminal meeting/lifecycle row is retained with an artifact-deletion tombstone. Returns
        ``None`` for unknown/unowned and ``False`` if the meeting is no longer terminal. Repeated
        calls on the tombstone are successful no-ops.
        """
        ...


@runtime_checkable
class PubSub(Protocol):
    """A redis-style pub/sub subscription (provided for symmetry with the gateway's RedisBus —
    the collector PUBLISHes, the gateway SUBSCRIBEs)."""

    async def subscribe(self, *channels: str) -> None: ...

    async def unsubscribe(self, *channels: str) -> None: ...

    async def close(self) -> None: ...

    def listen(self) -> AsyncIterator[dict]: ...


@runtime_checkable
class RedisBus(Protocol):
    """The bus the segment-ingestion worker consumes from and publishes to.

      * ``read_segments(...)`` — drain the ``transcription_segments`` stream (XREADGROUP in
        prod; a deterministic batch read in the eval) → ``[(message_id, fields), ...]``.
      * ``ack(...)`` — acknowledge processed message ids (XACK).
      * ``publish(channel, data)`` — fan a change-only update out on
        ``tc:meeting:{id}:mutable`` (the gateway ``/ws`` subscribes; ``services/redis.md``).

    Both redis-asyncio and fakeredis satisfy this shape; the eval calls ``ingest`` /
    ``consume_segments`` explicitly (no background loop), like the runtime scheduler's tick.
    """

    async def read_segments(
        self, *, group: str, consumer: str, stream: str, count: int = 10
    ) -> list[tuple[str, dict]]:
        ...

    async def reclaim_orphans(
        self, *, group: str, stream: str, consumer: str, min_idle_ms: int, count: int = 10
    ) -> list[tuple[str, dict]]:
        """#636: reclaim DELIVERED-but-un-acked entries idle longer than ``min_idle_ms`` from ANY
        consumer's PEL into ``consumer`` (XAUTOCLAIM) → ``[(message_id, fields), ...]``. A crashed
        replica's orphaned batch is otherwise never re-delivered; this is the seam a surviving
        replica uses to pick it up. One bounded call per tick (XAUTOCLAIM returns a continuation
        cursor; the next tick continues) — never loops-to-exhaustion inside one call."""
        ...

    async def list_consumers(
        self, *, group: str, stream: str
    ) -> list[dict]:
        """#660: enumerate the group's consumers (XINFO CONSUMERS) → ``[{"name", "pending", "idle"},
        ...]`` (idle in ms). The seam the reclaim sweep uses to find ABANDONED per-recreate ghosts.
        Degrades to ``[]`` on a Redis that lacks the command (same no-op-on-unsupported contract as
        ``reclaim_orphans``) so the consume path is never broken."""
        ...

    async def delete_consumer(self, *, group: str, stream: str, consumer: str) -> int:
        """#660: XGROUP DELCONSUMER — remove ``consumer`` from ``group``. Returns the number of
        pending entries the consumer held (0 for a safely-pruned ghost). The caller only ever deletes
        a consumer it has already confirmed holds ``pending == 0``."""
        ...

    async def ack(self, *, group: str, stream: str, message_ids: list[str]) -> None: ...

    async def publish(self, channel: str, data: str) -> Any: ...

    async def xadd(self, stream: str, payload: dict) -> Any:
        """Append one entry to a redis STREAM (``payload`` is the inner JSON, stored under the
        ``payload`` field). The collector is the SINGLE writer of the per-meeting native transcript
        feed ``tc:meeting:{native}`` (P23) — the copilot worker + terminal SSE read it."""
        ...
