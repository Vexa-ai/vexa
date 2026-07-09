"""collector — the transcript backend folded into meeting-api (was the standalone
``transcription-collector`` service; P2 unification).

The transcript read-side + segment-ingestion the gateway proxies ``/transcripts`` +
``/meetings`` + ``/ws/authorize-subscribe`` to. Relocated VERBATIM from the standalone
``transcription_collector`` package into ``meeting_api.collector`` — the same shipped code,
now a front-doored sub-package of the one meeting-api modular monolith (mounted by
``meeting_api.app.create_app`` alongside lifecycle / bot_spawn / recordings). Collaborators are
injected as PORTS so the SAME app/worker runs with real adapters in prod and in-process fakes in
tests — the O-API-1 conformance assertions therefore drive THIS shipped code.

Public surface (the front door):
  - ``create_app(store, redis, ...)`` — the FastAPI collector router-app: GET ``/transcripts/
    {platform}/{native_meeting_id}`` (api.v1 ``TranscriptionResponse``), GET ``/meetings`` (api.v1
    ``MeetingListResponse``), POST ``/ws/authorize-subscribe`` (the gateway ``/ws`` authorizer
    hop), ``/health``. (When mounted into the unified app its routes are merged in;
    standalone ``create_app`` is still used by the conformance harness + this module's tests.)
  - ``ingest(store, redis, message)`` / ``consume_segments(store, redis, ...)`` — the
    segment-ingestion unit: ``transcription_segments`` stream → store → publish
    ``tc:meeting:{id}:mutable``.
  - ``ports`` — the Protocols: ``TranscriptStore``, ``RedisBus`` (+ ``PubSub``).
  - ``adapters.build_production_app(...)`` — wire ``create_app`` with real SQLAlchemy + redis.
  - ``fakes`` — ``InMemoryTranscriptStore`` / ``FakeRedisBus`` (offline drivers).
  - ``obs`` — the collector hop's ``logevent.v1`` trace emitter (``TraceMiddleware``, ``log_event``,
    the ``make_*`` factories the in-process conformance chain binds to the gateway's contextvars).

Import direction is one-way: the gateway conformance harness imports this sub-package to drive
the shipped collector; this package imports nothing from conformance.
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
