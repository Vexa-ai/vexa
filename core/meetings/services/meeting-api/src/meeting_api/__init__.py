"""meeting-api — the cloud control-plane service, ONE modular-monolith app (Python, P2).

Front door (P6): import from here, never a deep module path. ``create_app(...)`` assembles the
unified, uvicorn-able meeting-api by composing the front-doored sub-package modules below onto ONE
FastAPI app (``app.py``) — the v0.12 unification of the parent ``main.py``'s ``include_router`` list,
each module an isolated brick behind a port-seam.

**The unified app** — ``create_app(...)`` mounts:

* **lifecycle** (O-MTG-1, lifecycle.v1) — the bot lifecycle callback receiver + meeting-state FSM
  (POST ``/bots/internal/callback/lifecycle``). ``lifecycle.create_app(store)`` is the standalone
  receiver; ``lifecycle.LifecycleSink`` / ``MeetingStore`` the port + store.
* **bot_spawn** (invocation.v1 + runtime.v1) — POST ``/bots``: build the invocation + mint the
  MeetingToken + spawn the meeting-bot over the runtime kernel, eager-creating the MeetingSession.
* **collector** (api.v1) — the FOLDED-IN transcript backend (was the standalone
  transcription-collector): GET ``/transcripts``, GET ``/meetings``, POST ``/ws/authorize-subscribe``
  + the ``transcription_segments`` → ``tc:…:mutable`` consumer.
* **recordings** (recording.v1) — chunk upload + finalize → master in ``meeting.data`` JSONB.
* **sessions** — the ``MeetingSession`` model + the shared SQLAlchemy mirror (Meeting/Transcription/
  MeetingSession) every module binds.

**Library bricks** (driven by the flows above; wired by the P3 composition root):

* **webhooks** (O-MTG-2, webhook.v1) — outbound delivery behind a ``WebhookSink`` port: HMAC
  sign/verify, SSRF URL-guard, per-client event-filter, redis-backed retry queue + worker sweep.
* **scheduling** (O-MTG-3, schedule.v1) — compile a ``ScheduledBot{cron|at}`` into a job whose
  request is the ``POST /bots`` call, fired by a Clock-gated scheduler.

**Recording master codec** (recording.v1) — ``build_recording_master(chunks, media_format)``: the
golden-locked Python twin of ``@vexa/recording``'s ``buildRecordingMaster``.
"""
from . import bot_spawn, collector, lifecycle, recordings, sessions
from .app import create_app
from .recording_codec import build_recording_master

# ``scheduling`` (croniter) and ``webhooks`` (redis retry) are library bricks driven by the flows
# above; they are NOT on the unified app's HTTP path. Expose them LAZILY (PEP 562) so importing
# ``create_app`` / the app modules does not pull their heavier deps — a downstream consumer that
# only drives the REST surface (e.g. the gateway conformance harness) needs neither.
_LAZY = ("scheduling", "webhooks")


def __getattr__(name: str):
    if name in _LAZY:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "create_app",
    "build_recording_master",
    "lifecycle",
    "bot_spawn",
    "collector",
    "recordings",
    "sessions",
    "scheduling",
    "webhooks",
]
