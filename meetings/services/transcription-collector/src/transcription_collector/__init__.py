"""transcription_collector — the v0.12 PRODUCTION transcript backend.

The transcript backend the gateway proxies ``/transcripts`` + ``/meetings`` +
``/ws/authorize-subscribe`` to (the v0.12 carve of
``services/meeting-api/meeting_api/collector/``). Collaborators are injected as PORTS so the
SAME app/worker runs with real adapters in prod and in-process fakes in tests — the O-API-1
conformance assertions therefore drive THIS shipped code.

Public surface (the front door):
  - ``create_app(store, redis, ...)`` — the FastAPI collector: GET ``/transcripts/{platform}/
    {native_meeting_id}`` (api.v1 ``TranscriptionResponse``), GET ``/meetings`` (api.v1
    ``MeetingListResponse``), POST ``/ws/authorize-subscribe`` (the gateway ``/ws`` authorizer
    hop), ``/health``.
  - ``ingest(store, redis, message)`` / ``consume_segments(store, redis, ...)`` — the
    segment-ingestion unit: ``transcription_segments`` stream → store → publish
    ``tc:meeting:{id}:mutable``.
  - ``ports`` — the Protocols: ``TranscriptStore``, ``RedisBus`` (+ ``PubSub``).
  - ``adapters.build_production_app(...)`` — wire ``create_app`` with real SQLAlchemy + redis.
  - ``fakes`` — ``InMemoryTranscriptStore`` / ``FakeRedisBus`` (offline drivers).
  - ``obs`` — the lane's ``logevent.v1`` trace emitter (``TraceMiddleware``, ``log_event``).

Import direction is one-way: the gateway conformance harness imports THIS package to drive the
shipped collector; this package imports nothing from conformance.
"""
from __future__ import annotations

from .app import create_app
from .ingest import consume_segments, ingest
from .ports import PubSub, RedisBus, TranscriptStore

__all__ = [
    "create_app",
    "ingest",
    "consume_segments",
    "TranscriptStore",
    "RedisBus",
    "PubSub",
]
