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

    async def list_meetings(
        self,
        user_id: int,
        *,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict]:
        """The user's meetings, newest first — a list of api.v1 ``MeetingResponse``-shaped dicts
        (the body of ``MeetingListResponse``)."""
        ...

    async def authorize_subscribe(
        self, user_id: int, platform: str, native_meeting_id: str
    ) -> Optional[int]:
        """Resolve ``(user, platform, native_id)`` → the internal ``meeting_id`` the caller owns,
        or ``None`` when not owned/found. The DB ownership check IS the authorization boundary
        (``collector/endpoints.py`` ``ws_authorize_subscribe``)."""
        ...

    async def append_segment(self, meeting_id: int, segment: dict) -> None:
        """Persist one ingested transcript segment for ``meeting_id`` (keyed by its
        ``segment_id`` — stable identity, last-write-wins, exactly the collector's Redis-hash
        persistence)."""
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

    async def ack(self, *, group: str, stream: str, message_ids: list[str]) -> None: ...

    async def publish(self, channel: str, data: str) -> Any: ...

    async def xadd(self, stream: str, payload: dict) -> Any:
        """Append one entry to a redis STREAM (``payload`` is the inner JSON, stored under the
        ``payload`` field). The collector is the SINGLE writer of the per-meeting native transcript
        feed ``tc:meeting:{native}`` (P23) — the copilot worker + terminal SSE read it."""
        ...
