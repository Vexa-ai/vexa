"""bot_spawn — the ``POST /bots`` flow (build the invocation + mint the MeetingToken + spawn the
meeting-bot workload over runtime.v1, eager-creating the MeetingSession on spawn).

Front door (P6): import from here, never a deep module path.

Port of the parent ``meetings.request_bot`` CORE happy path. The bot's config is the sealed
``invocation.v1`` Invocation; the spawn is the sealed ``runtime.v1`` ``WorkloadSpec`` — both
validated at the seam before they ship. Collaborators (the DB, the runtime kernel) are injected as
PORTS so the same flow runs with real adapters in prod and in-process fakes in tests.

continue_meeting / max-bots / join-retry are P3 — NOT built here; the seams are marked in
``service.request_bot``.

Public surface:
  * ``build_router(repo, runtime)`` — the mountable ``POST /bots`` router (the unified app mounts it).
  * ``request_bot(...)`` — the spawn flow (the router's core; callable directly in tests).
  * ``build_invocation`` / ``build_workload_spec`` / ``mint_meeting_token`` — the
    invocation.v1 / runtime.v1 builders + the MeetingToken minter.
  * ``MeetingRepo`` / ``RuntimeClient`` ports + ``QuotaExceeded`` / ``SpawnFailed`` /
    ``DuplicateMeeting``.
  * ``adapters.build_production_router(...)`` — wire with real SQLAlchemy + the httpx runtime client.
  * ``fakes`` — ``InMemoryMeetingRepo`` / ``FakeRuntimeClient`` (offline drivers).
"""
from __future__ import annotations

from .invocation import build_invocation, build_workload_spec, mint_meeting_token
from .ports import MeetingRepo, QuotaExceeded, RuntimeClient, SpawnFailed
from .router import build_router
from .service import DuplicateMeeting, construct_meeting_url, request_bot

__all__ = [
    "build_router",
    "request_bot",
    "construct_meeting_url",
    "build_invocation",
    "build_workload_spec",
    "mint_meeting_token",
    "MeetingRepo",
    "RuntimeClient",
    "QuotaExceeded",
    "SpawnFailed",
    "DuplicateMeeting",
]
