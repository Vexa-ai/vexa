"""List-view shaping for the meetings list (#584).

The meetings-list endpoints (``GET /bots``, ``GET /meetings``) return a row PER meeting. Each row used
to embed that meeting's full ``data`` JSONB — transcripts, speaker events, logs, recordings, and
calendar event snapshots. Those details can make a single page multi-megabyte even though no list
consumer renders them.

We cannot drop ``data`` from the list wholesale — the list genuinely renders a few LIGHT keys from it
(a meeting's ``title``, connected ``docs``, ``scheduled_at``, the recording/transcribe flags). So the
list keeps those light keys and drops only the heavy detail keys — the ones that made the response
multi-MB and that the list never renders. The detail path (``GET /meetings/{id}`` and the transcript
endpoints) still ships the rest of ``data``.

Size is not the only reason a key stays off a response. ``meeting.data`` is a shared row blob that
also carries operational state — webhook signing config, share grants, session paths — which is not
meeting content and which the read's access rule (owner OR transcript-share recipient OR bound-
workspace member) would otherwise hand to a second party.

That state is not one bucket, and treating it as one is what #1243 got wrong. It splits in two by
WHO may see it, so the response projection is VIEWER-AWARE:

* :data:`SENSITIVE_OMIT_KEYS` — credential material and other people's material. Off every edge for
  every viewer, the OWNER INCLUDED: the signing secret, the per-link share grants, the S3 session
  path. The owner has dedicated endpoints for all three and no meeting response needs to carry them.
* :data:`OWNER_ONLY_KEYS` — owner-private but not credentials: the webhook endpoint + event filter
  and the reader roster. The owner's own configuration on the owner's own meeting, which the v0.10
  REST contract promises them (``compat/v010_rest_compat_test.py::test_10_user_webhook_config_flow``
  reads ``data.webhook_url`` back from ``GET /meetings``). Stripped for anyone who is not the owner —
  a transcript-share recipient receives the transcript, not the owner's endpoint configuration, and
  must not be able to enumerate who else the meeting was shared with.

Ownership is not re-derived here. Every caller already computed it — ``list_meetings`` builds its
access union and stamps ``shared``, ``get_transcript_by_id`` evaluates an explicit owner branch,
``update_planned_meeting`` is owner-scoped in SQL — so the decision is PASSED DOWN as
``viewer_is_owner``. It defaults to ``False``, so a caller that forgets to thread it gets the STRICT
view; the failure direction is a missing field, never a disclosure.

This module holds what the real store (``adapters.py``), the router (``app.py``) and the in-memory
fake (``fakes.py``) must share so they can never diverge: the heavy keys dropped from a list row, the
two tiers of response omissions, and the default page size that bounds an otherwise-unbounded list.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Heavy per-meeting ``data`` keys the list NEVER renders — dropped from list rows. Everything else
# (title, docs, scheduled_at, calendar_connection_id, calendar_uid, workspace_id,
# constructed_meeting_url, recording/transcribe flags, …) rides along, because the list DOES render
# some of it. ``calendar_sources`` is the one mixed-weight key: Calendar needs its source identity
# and auto-join policy, but not the embedded raw ICS event snapshot. It is projected separately
# below. Full ``data`` stays on ``GET /meetings/{id}``.
LIST_OMIT_KEYS = frozenset({
    "speaker_events",
    "bot_logs",
    "recordings",
    "status_transition",
    "chat_messages",
    "error_details",
    "last_error",
})

CALENDAR_SOURCE_LIST_KEYS = frozenset({
    "id",
    "name",
    "auto_join",
    "bot_name",
})

# ── Response-edge omissions · TIER 1: never shipped, to anyone ───────────────────────────────────
# Credential material, and material belonging to people other than the reader. Dropped on EVERY
# response edge for EVERY viewer INCLUDING THE OWNER — not because the owner is untrusted, but
# because no meeting response is the right carrier for them and each already has a purpose-built
# surface. This matches how the delivery path treats the same blob
# (``webhooks.delivery._INTERNAL_DATA_KEYS``) and how ``GET /user/webhook`` reports the config
# (``webhook_secret_set`` + a masked value, never the value).
SENSITIVE_OMIT_KEYS = frozenset({
    # The webhook SIGNING SECRET. It is the entire security property of the webhook: anyone holding
    # it can forge a delivery the receiver will verify. The owner reads its presence from
    # ``GET /user/webhook``; nothing needs the value back out of a meeting row.
    "webhook_secret",
    "webhook_secrets",
    # Per-link share grants: each carries its ``secret_hash`` plus the allow-list and expiry. This is
    # the credential half of the share machinery, and the redeem path is its only legitimate reader.
    "share_grants",
    # S3 path of the authenticated browser-session userdata used for authenticated spawns — a
    # pointer to a live browser session's cookies.
    "auth_userdata_path",
})

# ── Response-edge omissions · TIER 2: the OWNER's, and only the owner's ──────────────────────────
# Owner-private, but NOT credentials — the owner's own configuration and roster on the owner's own
# meeting. Shipped to the owner (v0.10 promises the webhook config back on the meeting row and
# ``test_10_user_webhook_config_flow`` reads it there), stripped for every other viewer: a
# transcript-share recipient was given a transcript, not the owner's endpoint configuration, and
# must not be able to enumerate the meeting's other readers.
OWNER_ONLY_KEYS = frozenset({
    # Per-user webhook config, stamped onto the row at spawn so the lifecycle callback can deliver.
    # The endpoint URL and the event filter are the owner's configuration — part of the v0.10 REST
    # contract, and the reason #1243's unconditional strip was a backward-compatibility break.
    "webhook_url",
    "webhook_events",
    # Delivery bookkeeping written back by the lifecycle callback: attempts, statuses, last error.
    # The owner's operational state; meaningless and none of their business to anyone else.
    "webhook_delivery",
    "webhook_deliveries",
    "outbound_events",
    # The reader roster — every user id that can read this meeting. A share recipient enumerating it
    # learns who ELSE the owner shared with, which is other people's material, not theirs.
    "transcript_viewers",
})

# What a NON-OWNER's response drops explicitly: both tiers. Kept as one name because that is the
# question most readers of this module are asking ("what does a stranger not get?"), and because the
# delivery path's internal-key set is pinned against it.
RESPONSE_OMIT_KEYS = SENSITIVE_OMIT_KEYS | OWNER_ONLY_KEYS

# DENY-set, not allow-list, deliberately (unchanged from #1243). ``data`` is an open multi-producer
# blob whose LIGHT keys the detail view genuinely renders (title, docs, notes, scheduled_at, flags,
# completion_reason, constructed_meeting_url, calendar_uid, …) and product work adds new ones
# continuously. An allow-list at this edge would silently blank every future feature's field on the
# detail page — a data-loss failure that no existing test would catch, surfacing as a bug report.
#
# Credential-shaped key-name endings dropped in addition to the sets above, so a key a future
# producer stamps into ``data`` is omitted by DEFAULT rather than shipped until someone notices.
# This is a TIER-1 rule: a credential-shaped key is withheld from the owner too, because the whole
# point is that nobody has yet decided what it is. Matched on the whole key or its ``_``-suffixed
# tail, so ``token_count``/``secret_santa_notes`` and other names that merely CONTAIN a sensitive
# word are unaffected.
SENSITIVE_KEY_SUFFIXES = (
    "secret", "secrets", "token", "tokens", "password", "credential", "credentials",
    "api_key", "apikey", "private_key", "signing_key", "access_key",
)

# Default page size applied on the list-view path when a caller passes no ``limit`` — turns an
# unbounded full-table response (the outage's proximate trigger) into a bounded page. An explicit
# ``limit`` still wins. Internal callers that reuse ``list_meetings`` to enumerate ALL of a user's
# meetings (get-by-id filter, /bots/status, calendar sync) do NOT take the list-view path and are
# never capped.
DEFAULT_LIST_LIMIT = 50


# #1222: the list orders by the MEETING EVENT time, not row-creation time. A calendar-managed row
# is created at IMPORT time — possibly days before the meeting — so `created_at DESC` buried a
# meeting that was live RIGHT NOW under every row created since the import (witnessed in production
# 2026-08-18: the founder, in the meeting, could not find it in the list). Two-part sort key, shared
# verbatim by the real store's SQL (`meeting_event_time()` + the status pin, adapters.py) and the
# in-memory fake so they can never diverge:
#   1. non-terminal rows pin ABOVE terminal ones — the meeting happening (or about to happen) always
#      leads the list;
#   2. within each group, event time DESC: COALESCE(data.scheduled_at, start_time, created_at).
# `needs_help` is FSM-non-terminal but deliberately NOT pinned — the pin set is the issue's ruling
# (scheduled/requested/joining/awaiting_admission/active/stopping), and a needs-help row still ranks
# by its event time.
LIST_PIN_STATUSES = frozenset({
    "scheduled", "requested", "joining", "awaiting_admission", "active", "stopping",
})


def _event_ts(value: Any) -> Optional[str]:
    """One timestamp → a lexicographically comparable naive-UTC ISO string, or None.

    Accepts the shapes the stores hold: ISO-8601 strings (with `Z`, an offset, or naive = UTC)
    and datetimes. Malformed input is None — the caller falls through COALESCE-style, exactly
    like the SQL `meeting_event_time()`'s exception guard."""
    from datetime import datetime, timezone

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def list_order_key(meeting: Dict[str, Any]) -> tuple:
    """The list-view sort key for one meeting row (sort with ``reverse=True``).

    ``(pinned, event_time)`` — the caller appends its own stable tiebreak (row id)."""
    data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
    event = (_event_ts(data.get("scheduled_at"))
             or _event_ts(meeting.get("start_time"))
             or _event_ts(meeting.get("created_at"))
             or "")
    return (meeting.get("status") in LIST_PIN_STATUSES, event)


def project_calendar_sources(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` with each calendar source reduced to :data:`CALENDAR_SOURCE_LIST_KEYS`.

    The stored source carries ``event`` — the whole raw ICS component: every attendee address,
    the organizer, the description, the conference data. That snapshot is internal reconciliation
    state for the sweep; no API consumer renders it. So it stays in the row and never rides a
    response, on ANY read path and for EVERY viewer — a meeting reaches workspace members and
    transcript-share recipients too, and the owner has no use for it either.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched. A
    non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    sources = data.get("calendar_sources")
    if not isinstance(sources, list):
        return dict(data)
    projected = dict(data)
    projected["calendar_sources"] = [
        {k: v for k, v in source.items() if k in CALENDAR_SOURCE_LIST_KEYS}
        for source in sources
        if isinstance(source, dict) and source.get("id")
    ]
    return projected


def is_sensitive_key(key: str) -> bool:
    """True when ``key`` is named like credential material (see :data:`SENSITIVE_KEY_SUFFIXES`)."""
    k = key.lower()
    return any(k == s or k.endswith("_" + s) for s in SENSITIVE_KEY_SUFFIXES)


def omitted_keys(*, viewer_is_owner: bool) -> frozenset:
    """The explicit key set a response drops for this viewer.

    ``viewer_is_owner`` is the caller's ALREADY-COMPUTED access decision, passed down rather than
    re-derived (see the module docstring). It defaults nowhere: every call site names it, and the
    two public projections below default it to ``False`` so an un-threaded caller gets the strict
    view.
    """
    return SENSITIVE_OMIT_KEYS if viewer_is_owner else RESPONSE_OMIT_KEYS


def project_response_data(
    data: Optional[Dict[str, Any]], *, viewer_is_owner: bool = False,
) -> Dict[str, Any]:
    """Return ``data`` shaped for an API RESPONSE **to this viewer**: calendar sources reduced to
    their identity + policy keys, credential material dropped for everyone, and the owner's private
    configuration dropped for everyone but the owner.

    This is the projection for the DETAIL paths (``GET /meetings/{id}``, the transcript endpoints,
    the ``PATCH`` echo). Those reads authorize the owner, a transcript-share recipient, AND a member
    of the bound workspace, so "the owner put it there" is not a reason for a key to ride the
    response to a SECOND party — but it is exactly the reason the OWNER still gets their own webhook
    configuration back, which the v0.10 REST contract promises them.

    ``viewer_is_owner`` defaults to ``False`` (the strict view): a caller that has not threaded the
    access decision fails toward a missing field, never toward a disclosure.

    Pure and non-mutating (builds a new dict). It operates on the RESPONSE, never on the stored row,
    so state the system genuinely needs — the webhook signing secret the lifecycle callback reads
    from ``meeting_row["data"]`` at delivery time, the share grants the redeem path matches against,
    the ICS snapshot the calendar sweep reconciles — is untouched in the database. A non-dict
    ``data`` projects to ``{}``.
    """
    omit = omitted_keys(viewer_is_owner=viewer_is_owner)
    projected = project_calendar_sources(data)
    return {
        k: v for k, v in projected.items()
        if k not in omit and not is_sensitive_key(k)
    }


def project_list_data(
    data: Optional[Dict[str, Any]], *, viewer_is_owner: bool = False,
) -> Dict[str, Any]:
    """Return ``data`` with the heavy :data:`LIST_OMIT_KEYS` dropped; every light key kept.

    The list is a response edge too — and the one where a share recipient first meets a meeting they
    do not own — so it applies the same viewer-aware omissions as the detail path.
    ``LIST_OMIT_KEYS`` is about SIZE (keys no list row renders) and is unconditional; the response
    omissions are about DISCLOSURE and depend on who is reading.

    The owner branch is load-bearing, not a convenience: a 0.10 client reads its applied
    ``webhook_url`` back from ``GET /meetings`` (``test_10_user_webhook_config_flow``), so this edge
    is where the v0.10 webhook-config contract actually lives.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched. A
    non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    omit = omitted_keys(viewer_is_owner=viewer_is_owner)
    sources = project_calendar_sources(data).get("calendar_sources")
    projected = {k: v for k, v in data.items()
                 if k not in LIST_OMIT_KEYS and k != "calendar_sources"
                 and k not in omit and not is_sensitive_key(k)}
    if isinstance(sources, list):
        projected["calendar_sources"] = sources
    return projected
