"""meeting-api — the cloud control-plane service (Python).

Front door (P6): import from here, never a deep module path. This carve holds the
control-plane pieces that need no live meeting/bot/docker to prove out:

**Recording master codec** (recording.v1) — the Python twin of ``@vexa/recording``'s
``buildRecordingMaster``, golden-locked against the shared vectors:

* ``build_recording_master(chunks, media_format)`` — webm byte-concat / wav header-merge.

**Lifecycle receiver + meeting-state machine** (O-MTG-1, lifecycle.v1) — ingests the
bot's domain-status events and drives each meeting record's FSM:

* ``lifecycle.create_app(store)`` — the FastAPI receiver (validates at the seam, 409s
  illegal transitions); ``lifecycle.LifecycleSink`` / ``MeetingStore`` — the port + store.

**Webhooks** (O-MTG-2, webhook.v1) — outbound delivery (system + per-client) behind a
``WebhookSink`` port: HMAC sign/verify over ``ts.payload``, SSRF URL-guard, per-client
event-filter, and a redis-backed exponential-backoff retry queue + worker sweep.

* ``webhooks.WebhookSink`` / ``build_envelope`` / ``verify_signature`` /
  ``validate_webhook_url`` / ``RetryQueue`` / ``drain_retry_queue``.

**Scheduling** (O-MTG-3, schedule.v1) — compile a ``ScheduledBot{cron|at}`` into a
``schedule.v1`` job whose request is the ``POST /bots`` bot-spawn call, fired by a
Clock-gated scheduler (capturing dispatch in evals → no bot spawns), cron jobs re-arm,
cancel removes:

* ``scheduling.ScheduledBot`` / ``compile_scheduled_bot`` / ``conforms`` /
  ``Scheduler`` / ``FakeClock``.
"""
from . import lifecycle, scheduling, webhooks
from .recording_codec import build_recording_master

__all__ = ["build_recording_master", "lifecycle", "scheduling", "webhooks"]
