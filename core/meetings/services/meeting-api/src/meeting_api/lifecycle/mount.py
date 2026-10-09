"""The lifecycle HTTP mount on the unified meeting-api app.

Owns the two callback routes a meeting's lifecycle arrives on, and the one in-process entry both of
them drive:

  * ``POST /bots/internal/callback/lifecycle`` — the bot's own ``lifecycle.v1`` events;
  * ``POST /runtime/callback`` — the runtime kernel's workload events, whose confirmed terminal
    states drive a synthetic terminal through the same entry;
  * ``app.state.apply_lifecycle_event`` — advance the FSM for one event and run every side effect
    (persist, finalize, webhooks, flows publish, ws publish, copilot reap).

``app.create_app`` calls :func:`mount_lifecycle`; nothing else mounts these routes.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import events as _flows_events
from .. import meeting_token
from .machine import LifecycleSink

if TYPE_CHECKING:
    from ..bot_spawn import MeetingRepo

#: In-process capture of the last N emitted webhook envelopes — an eval/introspection seam, never a
#: durable store (the DB meeting row is the durable record; the WebhookSink is the delivery path).
#: BOUNDED because it lives on the production app and every bot lifecycle callback appends one
#: envelope that embeds the meeting's ``data`` projection; an unbounded list grew RSS monotonically
#: under production callback traffic while idle staging (no callbacks) stayed flat (#803). A ring
#: buffer keeps the recent-envelope semantics every reader relies on (``[-1]``, ``len``, iteration)
#: while capping retention.
_ENVELOPE_LOG_CAP = 256


def _webhook_target_host(url: str) -> str:
    """Host of a webhook URL, for the delivery log. Never the full URL: a subscriber's endpoint can
    carry a token in its path or query, and an operator reading delivery outcomes does not need it."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or "?"
    except Exception:  # noqa: BLE001 — a log field must never break delivery
        return "?"


def mount_lifecycle(
    app: FastAPI,
    sink: LifecycleSink,
    meeting_repo: "MeetingRepo",
    webhook_sink: "object" = None,
    system_webhook_sink: "object" = None,
    redis: "object" = None,
    transcript_finalizer: "object" = None,
    delivery_ledger: "object" = None,
    *,
    callback_secret: Optional[str] = None,
    internal_secret: Optional[str] = None,
    bot_redis: Optional["object"] = None,
    runtime_callback_token: Optional[str] = None,
) -> None:
    """Register the lifecycle.v1 callback route on the unified app (the lifecycle receiver's
    ``/bots/internal/callback/lifecycle`` handler, sharing the app's TraceMiddleware).

    A bot's callback is authenticated by the MeetingToken minted for ITS session: the token must be
    signed with ``callback_secret`` (the MeetingToken key, ``ADMIN_TOKEN``) and bound to the
    event's ``connection_id``. The internal tier (``x-internal-secret``) is also accepted. The
    production entrypoint always passes ``callback_secret``; ``None`` is the in-process harness,
    which drives the route directly.

    P3a — each FSM advance emits the sealed ``meeting.status_change`` webhook.v1 envelope and
    records the full diagnostics (``status_transition[]`` + forensics in ``rec.data``). The
    receiver is a bot callback → ``transition_source=bot_callback``. Each advance is ALSO persisted
    to the DB meeting row via ``meeting_repo`` (durable + queryable status, not only the in-process
    store). Also mounts ``POST /runtime/callback`` so the runtime kernel's workload callbacks ACK
    (no 404-retry).

    Before applying an event the callback REHYDRATES the in-memory FSM record from the DB meeting
    status, so the FSM survives a process restart (the in-process store starts empty) and a terminal
    callback reconciles against the durable status. After a persisted advance it PUBLISHES a ws.v1
    ``BotStatus`` frame to ``bm:meeting:{id}:status`` for the gateway ``/ws`` to forward to clients.
    """
    import jsonschema

    from .machine import IllegalTransition, TransitionSource
    from .provenance import build_service_provenance
    from .receiver import conforms
    from .webhook import build_status_change_envelope, build_typed_envelope
    from ..obs import log_event
    from ..webhooks import clean_meeting_data

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _meeting_projection_from_row(row: dict) -> dict:
        """The parent's `_build_meeting_event_data` shape (webhooks.py) from a meeting row dict —
        the meeting block the typed webhooks carry (golden Envelope.meeting-completed.json).
        completion_reason/failure_stage are hoisted to top level; internal data keys stripped."""
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "platform": row.get("platform"),
            "native_meeting_id": row.get("native_meeting_id"),
            "constructed_meeting_url": row.get("constructed_meeting_url"),
            "status": row.get("status"),
            "completion_reason": data.get("completion_reason"),
            "failure_stage": data.get("failure_stage"),
            "service_provenance": data.get("service_provenance"),
            "start_time": _iso(row.get("start_time")),
            "end_time": _iso(row.get("end_time")),
            "data": clean_meeting_data(data),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
        }

    app.state.status_change_webhooks = deque(maxlen=_ENVELOPE_LOG_CAP)
    app.state.typed_webhooks = deque(maxlen=_ENVELOPE_LOG_CAP)

    async def _apply_lifecycle_event(
        body: dict,
        *,
        transition_source: "TransitionSource" = TransitionSource.BOT_CALLBACK,
        force_terminal_on_destroy: bool = False,
    ) -> tuple[int, dict]:
        """Apply ONE lifecycle.v1 event to the FSM + run every side effect (persist, finalize,
        webhook deliver, ws publish, copilot reap), returning ``(status_code, content)``.

        This is the SINGLE in-process entry the FSM advance flows through — the HTTP endpoint
        ``POST /bots/internal/callback/lifecycle`` (the bot's own callback) is a thin wrapper around
        it, and the runtime-callback synthetic-terminal path calls it DIRECTLY (no HTTP self-POST).
        The prior implementation POSTed to ``http://127.0.0.1:PORT/…`` to re-enter this logic; that
        loopback round-trip was fragile (a rehydration race made the synthetic terminal 409, and
        under any harness that cannot reach the loopback it silently dropped) — the direct in-process
        call removes the network hop entirely, so the synthetic terminal advances the SAME FSM
        instance deterministically. ``force_terminal_on_destroy`` rides through to the sink so a
        runtime-confirmed destroy can force the terminal edge from a stale non-terminal state."""
        try:
            conforms(body, "LifecycleEvent")
        except jsonschema.ValidationError as e:
            log_event(
                "lifecycle_event_rejected", audience="system", level="warning",
                span="lifecycle.callback",
                fields={"reason": "schema_violation", "detail": e.message},
            )
            return (
                422,
                {"status": "error", "detail": f"lifecycle.v1 schema violation: {e.message}"},
            )
        # LIFECYCLE-409 fix: rehydrate the in-memory FSM record from the DB's CURRENT status before
        # applying the event. The in-memory MeetingStore is non-durable — after a meeting-api restart
        # it is empty, so a bot's terminal `completed` event would land on a fresh status=None record
        # → can_transition(None, COMPLETED) is False → IllegalTransition → 409, the bot retries 3x,
        # all 409, and the meeting stays stuck `active`. Seeding the record from the persisted status
        # first makes active/stopping → completed a legal transition again. Best-effort: a DB hiccup
        # must never fail the callback (we fall back to the in-process record as-is).
        connection_id = body.get("connection_id")
        if connection_id:
            existing = sink.store.get(connection_id)
            # A TERMINAL event also re-reads the row, even for a record this process already
            # advanced. The user-stop flag is written to the DB by the stop path and NEVER through
            # this FSM, so an in-process record that has seen `joining` has no way to know a DELETE
            # landed — and it is exactly at the terminal edge that the difference is written down
            # (F3, stage rev 193 row 26313: terminal recorded `join_failure` with
            # `stop_requested=true` on the row). One extra read per meeting, at its last event.
            terminal_event = body.get("status") in ("completed", "failed")
            if (
                existing is None
                or existing.status is None
                or (terminal_event and not existing.stop_requested)
            ):
                try:
                    persisted = await meeting_repo.get_lifecycle_state_by_session(
                        session_uid=connection_id
                    )
                except Exception as e:  # noqa: BLE001 — rehydration is best-effort
                    persisted = None
                    log_event("lifecycle_rehydrate_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(e)})
                if persisted:
                    sink.store.rehydrate(
                        connection_id,
                        persisted.get("status"),
                        persisted.get("data"),
                    )
        change = None
        try:
            change = sink.apply_change(
                body,
                transition_source=transition_source,
                force_terminal_on_destroy=force_terminal_on_destroy,
            )
        except IllegalTransition as first_error:
            # Multiple API replicas each hold an independent, non-durable FSM. A replica that saw
            # `joining` can receive `completed` after another replica persisted `active`/`stopping`.
            # Refresh only after the local edge is illegal: this lets durable state repair a stale
            # replica without letting a lagging DB read regress a live in-process record.
            persisted = None
            if connection_id:
                try:
                    persisted = await meeting_repo.get_lifecycle_state_by_session(
                        session_uid=connection_id
                    )
                except Exception as refresh_error:  # noqa: BLE001 — preserve truthful 409 below
                    log_event("lifecycle_refresh_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(refresh_error)})
            if persisted:
                sink.store.rehydrate(
                    connection_id,
                    persisted.get("status"),
                    persisted.get("data"),
                    replace_stale=True,
                )
                try:
                    change = sink.apply_change(
                        body,
                        transition_source=transition_source,
                        force_terminal_on_destroy=force_terminal_on_destroy,
                    )
                except IllegalTransition as refreshed_error:
                    first_error = refreshed_error
            if change is None:
                e = first_error
                return (
                    409,
                    {
                        "status": "error", "detail": str(e),
                        "connection_id": e.connection_id,
                        "from": e.frm.value if e.frm is not None else None,
                        "to": e.to.value,
                    },
                )
        rec = change.record
        # Build + record the status_change envelope only on a REAL advance — an idempotent replay
        # (change.no_op, e.g. the bot's 3x terminal retry) must NOT double-count it. The persist, the
        # webhook deliver, and the ws publish below are already no_op-gated (they hang off meeting_row,
        # set only on a real persist), so end-user delivery is exactly-once; this keeps the in-process
        # envelope log honest too.
        envelope = None
        if not change.no_op:
            envelope = build_status_change_envelope(change)
            app.state.status_change_webhooks.append(envelope)
        # Persist the FSM advance to the DB meeting row → durable + queryable (GET /meetings reflects
        # it, survives a restart), not only the in-process MeetingStore. Best-effort: a DB hiccup must
        # never fail the bot's lifecycle callback (the in-process FSM + webhook already advanced).
        # On an idempotent replay (change.no_op) the FSM did not actually advance — skip the
        # re-persist + re-deliver so a redelivered terminal does not fire a duplicate webhook /
        # publish. We still return 200 (handled below) — the redelivery is acknowledged as a no-op.
        meeting_row = None
        if rec.status is not None and not change.no_op:
            try:
                meeting_row = await meeting_repo.update_meeting_status(
                    session_uid=rec.connection_id,
                    status=rec.status.value,
                    completion_reason=rec.completion_reason.value if rec.completion_reason else None,
                    failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                    data=rec.data if isinstance(rec.data, dict) else None,
                )
            except Exception as e:  # noqa: BLE001 — persistence is best-effort
                log_event("lifecycle_persist_failed", audience="system", level="warning",
                          span="lifecycle.callback", fields={"error": str(e)})
        # COMPLETION FINALIZATION — the moment the FSM lands on a terminal status, flush the
        # meeting's remaining live redis segments to the durable store (threshold 0: the mutable
        # tail included, no more updates are coming) and persist the processed doc into
        # meeting.data, via the injected finalizer (prod: collector/db_writer.finalize_meeting).
        # This guarantees a completed meeting's transcript is durable even if the periodic
        # db-writer never gets another tick (crash/restart right after completion). Best-effort:
        # the periodic loop retries anything this misses; never fail the bot's callback.
        terminal_advanced = (
            not change.no_op
            and rec.status is not None
            and rec.status.value in ("completed", "failed")
            and isinstance(meeting_row, dict)
            and meeting_row.get("id") is not None
        )
        if (
            transcript_finalizer is not None
            and terminal_advanced
        ):
            terminal_meeting_id = meeting_row["id"]
            try:
                finalized_segments = await transcript_finalizer(terminal_meeting_id)
                if isinstance(finalized_segments, int) and finalized_segments >= 0:
                    rec_data = rec.data
                    rec_data["segments_captured"] = finalized_segments
                    updated_row = await meeting_repo.update_meeting_status(
                        session_uid=rec.connection_id,
                        status=rec.status.value,
                        completion_reason=(
                            rec.completion_reason.value if rec.completion_reason else None
                        ),
                        failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                        data=rec_data,
                    )
                    if isinstance(updated_row, dict):
                        meeting_row = updated_row
            except Exception as e:  # noqa: BLE001 — the db-writer loop is the retry path
                rec_data = rec.data
                rec_data["transcript_finalize_failed"] = True
                try:
                    updated_row = await meeting_repo.update_meeting_status(
                        session_uid=rec.connection_id,
                        status=rec.status.value,
                        completion_reason=(
                            rec.completion_reason.value if rec.completion_reason else None
                        ),
                        failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                        data=rec_data,
                    )
                    if isinstance(updated_row, dict):
                        meeting_row = updated_row
                except Exception:  # noqa: BLE001 — original finalization failure is the signal
                    pass
                log_event("transcript_finalize_failed", audience="system", level="warning",
                          span="lifecycle.callback",
                          fields={"meeting_id": terminal_meeting_id, "error": str(e)})
        if terminal_advanced and bot_redis is not None:
            # The session is over: its bot's Redis user goes with it (best-effort — the grant path
            # also removes any user older than its MeetingToken).
            try:
                await bot_redis.revoke(rec.connection_id)
            except Exception as e:  # noqa: BLE001
                log_event("bot_redis_revoke_failed", audience="system", level="warning",
                          span="lifecycle.callback", fields={"error": type(e).__name__})
        if terminal_advanced and isinstance(meeting_row, dict):
            data = dict(meeting_row.get("data") or {})
            provenance = build_service_provenance({**meeting_row, "data": data})
            if provenance is not None:
                data["service_provenance"] = provenance
                # The event projection can truthfully carry the producer facts even if this
                # best-effort persistence attempt fails, matching the callback's established
                # availability contract. The next reconciliation pass owns that exception.
                projection_row = {**meeting_row, "data": data}
                try:
                    updated_row = await meeting_repo.update_meeting_status(
                        session_uid=rec.connection_id,
                        status=rec.status.value,
                        completion_reason=(
                            rec.completion_reason.value if rec.completion_reason else None
                        ),
                        failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                        data=data,
                    )
                    meeting_row = (
                        updated_row if isinstance(updated_row, dict) else projection_row
                    )
                except Exception as e:  # noqa: BLE001 — callback availability is pre-existing
                    meeting_row = projection_row
                    log_event(
                        "service_provenance_persist_failed",
                        audience="system",
                        level="warning",
                        span="lifecycle.callback",
                        fields={"meeting_id": meeting_row.get("id"), "error": str(e)},
                    )
        # Build the TYPED event the transition maps to (meeting.started on active,
        # meeting.completed with the post-meeting envelope on completion, bot.failed on terminal
        # failure) — additive alongside meeting.status_change, never instead of it. Built AFTER the
        # persist so the meeting block is the durable row projection (the parent's
        # _build_meeting_event_data shape) when the row is known; the FSM-record fallback otherwise.
        typed_envelope = None
        if not change.no_op:
            typed_envelope = build_typed_envelope(
                change,
                meeting=_meeting_projection_from_row(meeting_row)
                if isinstance(meeting_row, dict) else None,
            )
            if typed_envelope is not None:
                app.state.typed_webhooks.append(typed_envelope)
        # THE MEETINGS→FLOWS PUBLISH EDGE (F168/F181, ADR-0037 / PRD 46 decision 42.2). An ad hoc
        # bot — started via the MCP `request_meeting_bot`, never through a calendar invite — has no
        # `invite_intake` reaction running for it, and that flow is the only thing that has ever
        # told flows a meeting started or finished (`emit_started` / `emit_completed`, PRD decision
        # 42.2). meeting-api's only outbound door used to be the operator webhook just above, which
        # flows does not read. So this fires on exactly the two typed transitions that door already
        # watches — one new domain telling flows a fact only meeting-api can know, alongside (never
        # instead of) the webhook.
        #
        # THE SOURCE_EVENT_ID DELIBERATELY MATCHES flows' OWN SCHEME (`live-<id>` / `done-<id>`,
        # `lifecycle/webhook.py` / `flows_defs/production.py`), not a meeting-api-flavoured one.
        # Admission dedups on `(source_event_id, flow)` (flows/admission.py), so for a
        # calendar-intake meeting — where `invite_intake` ALSO emits the same two facts from
        # inside itself — whichever producer's HTTP call lands first admits and the other is a
        # free no-op, rather than a second reaction (a second `post_meeting` run is a duplicate
        # email, not a no-op). A different id here would double-fire every calendar meeting.
        # See `events.py` for the ref-richness trade this same choice carries: meeting-api holds
        # no invite (no `participants`/`group`), so on `meeting.completed` for a calendar meeting
        # meeting-api's own publish typically WINS the race (it fires the instant the DB row goes
        # `completed`; flows' own `emit_completed` only fires after its next poll tick), and
        # `process_meeting`'s room-read degrades to empty rather than to invite order — see the
        # filed issue for this deployment's disposition of that trade-off.
        if typed_envelope is not None and isinstance(meeting_row, dict):
            _meeting_block = (typed_envelope.get("data") or {}).get("meeting")
            if isinstance(_meeting_block, dict):
                _et = typed_envelope.get("event_type")
                _uid = _meeting_block.get("user_id")
                if _uid is not None and _et == "meeting.started":
                    try:
                        await _flows_events.publish_meeting_started(
                            _meeting_block.get("id"),
                            _meeting_block.get("native_meeting_id"),
                            _meeting_block.get("platform"),
                            _uid,
                        )
                    except Exception:  # noqa: BLE001 — a publish edge is not a dependency
                        pass
                elif _uid is not None and _et == "meeting.completed":
                    try:
                        await _flows_events.publish_meeting_completed(
                            _meeting_block.get("id"),
                            _meeting_block.get("native_meeting_id"),
                            _meeting_block.get("platform"),
                            _uid,
                            _meeting_block.get("completion_reason"),
                        )
                    except Exception:  # noqa: BLE001 — a publish edge is not a dependency
                        pass
        # The operator callback is a separate trust boundary from a customer's
        # webhook. It receives terminal service facts only, through a destination
        # frozen by deployment config. A transient failure is retained by the
        # sink's dedicated retry queue; it never falls back to a user URL.
        if (
            system_webhook_sink is not None
            and typed_envelope is not None
            and typed_envelope.get("event_type") in {
                "meeting.completed",
                "bot.failed",
            }
        ):
            try:
                result = await system_webhook_sink.deliver(
                    typed_envelope,
                    label=f"meeting:{meeting_row.get('id')}"
                    if isinstance(meeting_row, dict)
                    else f"session:{rec.connection_id}",
                )
                if result is not None:
                    log_event(
                        "system_webhook_delivery",
                        audience="system",
                        level=(
                            "info"
                            if result.status == "delivered"
                            else "warning"
                        ),
                        span="lifecycle.callback",
                        meeting_id=(
                            meeting_row.get("id")
                            if isinstance(meeting_row, dict)
                            else None
                        ),
                        fields={
                            "outcome": result.status,
                            "event_type": typed_envelope.get("event_type"),
                            "status_code": result.status_code,
                        },
                    )
            except Exception as e:  # noqa: BLE001 — terminal fact stays in meeting storage
                log_event(
                    "system_webhook_delivery_failed",
                    audience="system",
                    level="warning",
                    span="lifecycle.callback",
                    meeting_id=(
                        meeting_row.get("id")
                        if isinstance(meeting_row, dict)
                        else None
                    ),
                    fields={"error_type": type(e).__name__},
                )
        # Deliver the sealed webhook.v1 envelopes (meeting.status_change + the typed event, if any)
        # to the user's configured endpoint (per-user config rides on meeting.data — set at spawn
        # from identity via the gateway; NO users-table read). The sink's per-user event filter
        # (webhooks/delivery.py) suppresses unsubscribed event types before any HTTP.
        # Best-effort: a delivery hiccup must never fail the bot's lifecycle callback (P3a).
        if webhook_sink is not None and isinstance(meeting_row, dict):
            data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
            url = data.get("webhook_url")
            if url:
                for env in (envelope, typed_envelope):
                    if env is None:
                        continue
                    try:
                        result = await webhook_sink.deliver(
                            url, env, data.get("webhook_secret"),
                            events_config=data.get("webhook_events"),
                            label=f"meeting:{meeting_row.get('id')}",
                        )
                        # EVERY outcome is reported (#815). `deliver` never raises — it returns
                        # delivered | suppressed | blocked | failed | queued — and the outcome used
                        # to be discarded, so a webhook the subscriber never received (unsubscribed
                        # event type, SSRF-blocked target, 4xx endpoint) was indistinguishable from
                        # one that arrived: "my webhooks stopped" was undiagnosable in production.
                        # The target is reported as host only — a webhook URL can carry a secret in
                        # its path or query, and logs are not a place to put one.
                        log_event(
                            "webhook_delivery",
                            audience="system",
                            level="info" if result.status == "delivered" else "warning",
                            span="lifecycle.callback",
                            meeting_id=meeting_row.get("id"),
                            fields={
                                "outcome": result.status,
                                "event_type": env.get("event_type"),
                                "target_host": _webhook_target_host(url),
                                "status_code": result.status_code,
                                "error": result.error,
                            },
                        )
                        # #841: ALSO record the outcome in the per-user delivery ledger — the
                        # queryable surface GET /webhooks/deliveries serves. Logs (above) rotate and
                        # are operator-facing; the ledger is the user's Delivery History. Host only,
                        # never the URL/secret (P14). Best-effort — a ledger hiccup never fails the
                        # callback, and a suppressed event is still worth recording (the user asked
                        # "why didn't my webhook fire?" — "suppressed: unsubscribed" is the answer).
                        if delivery_ledger is not None:
                            from ..webhooks import build_delivery_record

                            try:
                                await delivery_ledger.record(
                                    meeting_row.get("user_id"),
                                    build_delivery_record(
                                        event_type=env.get("event_type"),
                                        event_id=env.get("event_id"),
                                        target_host=_webhook_target_host(url),
                                        outcome=result.status,
                                        status_code=result.status_code,
                                        meeting_id=meeting_row.get("id"),
                                    ),
                                )
                            except Exception as le:  # noqa: BLE001 — ledger is best-effort
                                log_event("webhook_ledger_failed", audience="system",
                                          level="warning", span="lifecycle.callback",
                                          fields={"error": str(le)})
                    except Exception as e:  # noqa: BLE001 — delivery is best-effort
                        log_event("webhook_deliver_failed", audience="system", level="warning",
                                  span="lifecycle.callback", fields={"error": str(e)})
        # Publish each persisted FSM advance to bm:meeting:{id}:status in the canonical 0.10.6 WS
        # contract shape (the source of truth; api-gateway forwards the redis payload verbatim):
        #   {type:"meeting.status", meeting:{id,platform,native_id}, payload:{status}, user_id, ts}
        # `status` is the raw BotStatus value (e.g. 'needs_help'); clients translate to their own
        # vocabulary on THEIR side (the core emits the contract, never a client's naming). Skipped on
        # a no-op advance (idempotent replay) / unknown session. Best-effort: never fail the callback.
        if redis is not None and not change.no_op and isinstance(meeting_row, dict) and rec.status is not None:
            meeting_id = meeting_row.get("id")
            if meeting_id is not None:
                import json as _json
                from datetime import datetime, timezone

                frame = {
                    "type": "meeting.status",
                    "meeting": {
                        "id": meeting_id,
                        "platform": meeting_row.get("platform"),
                        "native_id": meeting_row.get("native_meeting_id"),
                    },
                    "payload": {"status": rec.status.value},
                    "user_id": meeting_row.get("user_id"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await redis.publish(f"bm:meeting:{meeting_id}:status", _json.dumps(frame))
                except Exception as e:  # noqa: BLE001 — publish is best-effort
                    log_event("ws_status_publish_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(e)})
                # ALSO publish the FLAT frame to the USER-scoped channel u:{user_id}:meetings so the
                # terminal's list surface gets every bot-FSM transition over WS (superset of bm:; it
                # also carries the pre-FSM idle/scheduled states). KEEP bm: above for the open-meeting
                # tab. Best-effort: never fail the lifecycle callback.
                #
                # A meeting BOUND TO A WORKSPACE also goes to `w:{workspace_id}:meetings`, the channel
                # every member's socket joins at connect. This is the transition members actually need
                # — requested → joining → active is the bot arriving in THEIR call — and publishing it
                # only to the owner is why a member's terminal showed nothing happening while the
                # meeting ran.
                user_id = meeting_row.get("user_id")
                if user_id is not None:
                    user_frame = {
                        "type": "meeting.status",
                        "meeting_id": meeting_id,
                        "native": meeting_row.get("native_meeting_id"),
                        "status": rec.status.value,
                        "when": frame["ts"],
                    }
                    workspace_id = (meeting_row.get("data") or {}).get("workspace_id")
                    channels = [f"u:{user_id}:meetings"]
                    if workspace_id:
                        channels.append(f"w:{workspace_id}:meetings")
                    for channel in channels:
                        try:
                            await redis.publish(channel, _json.dumps(user_frame))
                        except Exception as e:  # noqa: BLE001 — publish is best-effort
                            log_event("user_meeting_status_publish_failed", audience="system",
                                      level="warning", span="lifecycle.callback",
                                      fields={"error": str(e), "channel": channel})
        # COPILOT REAP (Bug 3): the moment a meeting lands TERMINAL, emit the `session_end` marker onto
        # the meeting copilot transcript feed — the EXACT stream the meeting copilot worker
        # (agent worker/meeting.py, via VEXA_TRANSCRIPT_STREAM) blocks on. The worker reaps immediately
        # on that marker (exit 0 → container reaped), instead of sitting idle for its
        # VEXA_IDLE_TIMEOUT_SEC (default 4h) when the bot never emitted its own `session_end` — e.g. it
        # was SIGKILLed, or stopped in the waiting room (Bug 2) before it could. Idempotent: a redundant
        # session_end (the bot already sent one via the collector) just reasserts the reap. Best-effort;
        # never fails the lifecycle callback.
        #
        # KEYING (P0 fix/transcript-cross-tenant-leak, now merged): the carrier is ROW-scoped
        # `tc:meeting:{meeting_row_id}` — the numeric meetings-domain ROW id, NOT the native id (which
        # collides across tenants/rows and is never a data key post-P0). The collector
        # (collector/ingest.py `_transcript_stream`) writes its session_end on the same row key and the
        # worker tails the row key (agent dispatch.py sets VEXA_TRANSCRIPT_STREAM=tc:meeting:{row_id}),
        # so this lifecycle reap must key by the row id to land on the live stream the worker blocks on.
        if (
            redis is not None
            and not change.no_op
            and rec.status is not None
            and rec.status.value in ("completed", "failed")
            and isinstance(meeting_row, dict)
            and hasattr(redis, "xadd")
        ):
            meeting_row_id = meeting_row.get("id")
            native = meeting_row.get("native_meeting_id") or rec.connection_id
            if meeting_row_id is not None:
                try:
                    await redis.xadd(
                        f"tc:meeting:{meeting_row_id}",
                        {"type": "session_end", "uid": str(native or meeting_row_id)},
                    )
                    log_event(
                        "meeting_copilot_reap_signalled", audience="system", span="lifecycle.callback",
                        meeting_id=rec.connection_id,
                        fields={"meeting_row_id": meeting_row_id, "native": native,
                                "meeting_status": rec.status.value},
                    )
                except Exception as e:  # noqa: BLE001 — the worker's idle timeout is the backstop
                    log_event("meeting_copilot_reap_failed", audience="system", level="warning",
                              span="lifecycle.callback",
                              fields={"meeting_row_id": meeting_row_id, "error": str(e)})
        # NEW-SESSION MARKER (mirror of the reap above): when a meeting goes ACTIVE, write a
        # `session_start` marker onto the same ROW-scoped stream. tc:meeting:{row_id} is SHARED across a
        # meeting's sessions — a re-sent bot REUSES a terminal row — so a prior session's `session_end`
        # lingers in the terminal SSE's replay tail and shows "Meeting ended" over the new live meeting.
        # This marker lets the SSE reset that stale end (agent api.py's seed treats any non-session_end
        # entry as "still live"), so even a SILENT new session clears the banner. Best-effort.
        if (
            redis is not None
            and not change.no_op
            and rec.status is not None
            and rec.status.value == "active"
            and isinstance(meeting_row, dict)
            and hasattr(redis, "xadd")
        ):
            meeting_row_id = meeting_row.get("id")
            native = meeting_row.get("native_meeting_id") or rec.connection_id
            if meeting_row_id is not None:
                try:
                    await redis.xadd(
                        f"tc:meeting:{meeting_row_id}",
                        {"type": "session_start", "uid": str(native or meeting_row_id)},
                    )
                except Exception as e:  # noqa: BLE001 — best-effort; the seed also resets on new segments
                    log_event("meeting_session_start_marker_failed", audience="system", level="warning",
                              span="lifecycle.callback",
                              fields={"meeting_row_id": meeting_row_id, "error": str(e)})
        log_event(
            "meeting_lifecycle_advanced", audience="user", span="lifecycle.callback",
            meeting_id=rec.connection_id,
            fields={"meeting_status": rec.status.value if rec.status else None},
        )
        return (
            200,
            {
                "status": "accepted",
                "connection_id": rec.connection_id,
                "meeting_status": rec.status.value if rec.status else None,
                "completion_reason": rec.completion_reason.value if rec.completion_reason else None,
                "failure_stage": rec.failure_stage.value if rec.failure_stage else None,
                "transition_source": change.transition_source.value,
                "status_transition": rec.status_transition,
                "data": rec.data,
            },
        )

    # Expose the in-process entry so the runtime-callback synthetic-terminal path can advance the FSM
    # DIRECTLY (no HTTP self-POST to 127.0.0.1:PORT). Same instance, same store, same side effects.
    app.state.apply_lifecycle_event = _apply_lifecycle_event

    def _bot_callback_admitted(request: Request, body: object) -> bool:
        """The event's own session's MeetingToken, or the internal tier."""
        import hmac

        if callback_secret is None:
            return True
        presented = request.headers.get("x-internal-secret") or ""
        if internal_secret and presented and hmac.compare_digest(presented.encode(), internal_secret.encode()):
            return True
        scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
        connection_id = body.get("connection_id") if isinstance(body, dict) else None
        if scheme.lower() != "bearer" or not token.strip():
            return False
        try:
            meeting_token.admit_session(token.strip(), session_uid=connection_id, secret=callback_secret)
        except meeting_token.InvalidMeetingToken:
            return False
        return True

    @app.post("/bots/internal/callback/lifecycle")
    async def lifecycle_callback(request: Request) -> JSONResponse:
        body = await request.json()
        if not _bot_callback_admitted(request, body):
            log_event("lifecycle_event_rejected", audience="system", level="warning",
                      span="lifecycle.callback", fields={"reason": "unauthenticated"})
            return JSONResponse(status_code=401,
                                content={"status": "error", "detail": "bot session credential required"})
        status_code, content = await _apply_lifecycle_event(
            body, transition_source=TransitionSource.BOT_CALLBACK
        )
        return JSONResponse(status_code=status_code, content=content)

    @app.post("/runtime/callback")
    async def runtime_callback(request: Request) -> JSONResponse:
        """ACK the runtime kernel's workload-level callback (state/terminal events). The bot's own
        ``lifecycle.v1`` callback is the meeting-status source of truth for a STARTED bot; this route
        ALSO consumes a runtime-confirmed TERMINAL workload state as evidence the run is over, driving a
        synthetic terminal through the SAME in-process lifecycle logic (no HTTP self-POST):

          * PRE-ACTIVE meeting → ``failed`` (CC5): the bot never started/reported and never will, so the
            meeting would otherwise hang ``requested``/``joining`` forever.
          * WAS-ACTIVE meeting (``stopping``/``active``/``needs_help``) → ``completed``: the bot reached
            the meeting but its workload is now runtime-confirmed gone WITHOUT its own terminal callback
            (e.g. SIGKILLed at teardown before it could POST ``completed``, or killed in the waiting room
            on a stop). Without this the meeting stays ``stopping`` and the stop-reconcile sweep re-DELETEs
            (now 404) every 15s FOREVER — the reaper loop. The confirmed destroy IS the terminal evidence
            (#50's principle: real evidence, not a bare 404) → complete it and stop the loop.

        THE FIX (live 409): the synthetic terminal is applied by calling the in-process lifecycle entry
        (``app.state.apply_lifecycle_event``) DIRECTLY with ``transition_source=RUNTIME_DESTROY`` and
        ``force_terminal_on_destroy=True`` — NOT an httpx POST to ``127.0.0.1:PORT``. The old self-POST
        409'd whenever the in-process FSM record was a stale non-terminal state the DB had already moved
        past (e.g. store still ``joining`` while the DB user-stop set ``stopping`` — ``joining →
        completed`` is illegal for a bot-driven edge). The direct in-process call advances the SAME FSM
        instance, and the runtime-destroy source forces the terminal edge on real teardown evidence, so
        the meeting reaches terminal, the reaper stops, and the copilot ``session_end`` reap fires."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        if runtime_callback_token is not None:
            from .. import runtime_signature

            if not runtime_signature.verify(runtime_callback_token, body,
                                            request.headers.get(runtime_signature.HEADER) or ""):
                log_event("runtime_callback_rejected", audience="system", level="warning",
                          span="runtime.callback", fields={"reason": "unsigned"})
                return JSONResponse(status_code=401, content={"status": "error",
                                                              "detail": "runtime signature required"})
        workload_id = body.get("workloadId") or body.get("workload_id")
        state = body.get("state")
        log_event(
            "runtime_callback", audience="system", span="runtime.callback",
            fields={"workload_id": workload_id, "state": state},
        )
        # Consume a runtime-confirmed TERMINAL workload as evidence (pre-active → failed / was-active →
        # completed). Drive it through the SAME in-process lifecycle logic (FSM/persist/webhook/ws/reap
        # all fire identically) — best-effort; a non-terminal state or an already-terminal meeting is a
        # no-op. Imported lazily to keep the prod import path lean.
        try:
            import logging as _logging

            from .machine import TransitionSource as _TS
            from .reconcile import synthesize_terminal_for_dead_workload

            async def _drive_terminal(event: dict):
                # In-process — no network hop. The runtime-destroy source forces the terminal edge past
                # a stale non-terminal FSM record; returns the HTTP-equivalent status code for the log.
                status_code, _content = await _apply_lifecycle_event(
                    event,
                    transition_source=_TS.RUNTIME_DESTROY,
                    force_terminal_on_destroy=True,
                )
                return status_code

            await synthesize_terminal_for_dead_workload(
                meeting_repo, workload_id, state, _drive_terminal,
                event_at=body.get("at"),
                log=_logging.getLogger("meeting_api.runtime.callback"),
            )
        except Exception as e:  # noqa: BLE001 — the runtime ACK must never fail on the terminal backstop
            log_event("runtime_callback_terminal_error", audience="system", level="warning",
                      span="runtime.callback", fields={"error": str(e)})
        return JSONResponse(status_code=200, content={"status": "accepted"})
