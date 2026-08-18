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
workspace member) would otherwise hand to a second party. Those keys (``RESPONSE_OMIT_KEYS``) are
dropped on EVERY response edge, list and detail alike.

This module holds what the real store (``adapters.py``), the router (``app.py``) and the in-memory
fake (``fakes.py``) must share so they can never diverge: the heavy keys dropped from a list row, the
keys dropped from every response, and the default page size that bounds an otherwise-unbounded list.
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

# ── Response-edge omissions ──────────────────────────────────────────────────────────────────────
# ``meeting.data`` is a shared row blob: several producers write operational state into it that is
# NOT the caller's meeting content. Some of that state is credential/authorization material, and a
# meeting row is readable by more than its owner — the access rule on every read here is owner OR
# transcript-share recipient OR member of the bound workspace (see ``list_meetings``' own comment).
# So these keys are dropped on EVERY response edge, for EVERY viewer including the owner, matching
# how the delivery path treats the same blob (``webhooks.delivery._INTERNAL_DATA_KEYS``) and how
# every other surface reports the webhook config (``GET /user/webhook`` → ``webhook_secret_set`` +
# a masked value, never the value).
#
# DENY-set, not allow-list, deliberately. ``data`` is an open multi-producer blob whose LIGHT keys
# the detail view genuinely renders (title, docs, notes, scheduled_at, flags, completion_reason,
# constructed_meeting_url, calendar_uid, …) and product work adds new ones continuously. An
# allow-list at this edge would silently blank every future feature's field on the detail page —
# a data-loss failure that no existing test would catch. The deny-by-default property an allow-list
# buys is recovered instead by :data:`SENSITIVE_KEY_SUFFIXES` below, which drops credential-SHAPED
# key names a future producer adds without anyone updating this set.
RESPONSE_OMIT_KEYS = frozenset({
    # Per-user webhook config, stamped onto the row at spawn so the lifecycle callback can sign
    # deliveries. The signing secret is the whole security property of the webhook; the URL and the
    # event filter are the owner's endpoint configuration. None of it is meeting content.
    "webhook_secret",
    "webhook_secrets",
    "webhook_url",
    "webhook_events",
    "webhook_delivery",
    "webhook_deliveries",
    "outbound_events",
    # Share/authz state: ``share_grants`` carries each link's ``secret_hash`` plus its allow-list and
    # expiry, and ``transcript_viewers`` is the roster of every user id that can read the meeting.
    # Both are the authorization machinery for this read, never its payload.
    "share_grants",
    "transcript_viewers",
    # S3 path of the authenticated browser-session userdata used for authenticated spawns.
    "auth_userdata_path",
})

# Credential-shaped key-name endings dropped in addition to :data:`RESPONSE_OMIT_KEYS`, so a key a
# future producer stamps into ``data`` is omitted by DEFAULT rather than shipped until someone
# notices. Matched on the whole key or its ``_``-suffixed tail, so ``token_count``/``secret_ballot``
# and other legitimate names that merely CONTAIN a sensitive word are unaffected.
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


def project_response_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` shaped for an API RESPONSE: calendar sources reduced to their identity +
    policy keys, and every credential/authorization key dropped.

    This is the projection for the DETAIL paths (``GET /meetings/{id}`` and the transcript
    endpoints), which ship ``data`` in full. Those reads authorize the owner, a transcript-share
    recipient, AND a member of the bound workspace, so "the owner put it there" is not a reason for
    a key to ride the response — the reader may be someone the owner shared one transcript with.

    Pure and non-mutating (builds a new dict). It operates on the RESPONSE, never on the stored row,
    so state the system genuinely needs — the webhook signing secret the lifecycle callback reads
    from ``meeting_row["data"]`` at delivery time, the share grants the redeem path matches against,
    the ICS snapshot the calendar sweep reconciles — is untouched in the database. A non-dict
    ``data`` projects to ``{}``.
    """
    projected = project_calendar_sources(data)
    return {
        k: v for k, v in projected.items()
        if k not in RESPONSE_OMIT_KEYS and not is_sensitive_key(k)
    }


def project_list_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` with the heavy :data:`LIST_OMIT_KEYS` dropped; every light key kept.

    The list is a response edge too — and the one where a share recipient first meets a meeting they
    do not own — so it drops :data:`RESPONSE_OMIT_KEYS` as well. ``LIST_OMIT_KEYS`` is about SIZE
    (keys no list row renders); the response omissions are about DISCLOSURE and apply on every path.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched. A
    non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    sources = project_calendar_sources(data).get("calendar_sources")
    projected = {k: v for k, v in data.items()
                 if k not in LIST_OMIT_KEYS and k != "calendar_sources"
                 and k not in RESPONSE_OMIT_KEYS and not is_sensitive_key(k)}
    if isinstance(sources, list):
        projected["calendar_sources"] = sources
    return projected
