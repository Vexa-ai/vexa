"""peer_lookups.py — how agent-api asks its two peers about a meeting or a person.

meeting-api answers whether a caller may read a meeting (`GET /meetings/{id}`) and what was said in
it (`GET /transcripts/by-id/{id}`), always AS the caller over the internal tier, so meeting-api's
own access union decides. admin-api answers which subject an email address belongs to, for the
post-meeting room. Each is built once in `create_app` and injectable for tests; every one fails
closed (None), never open.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

logger = logging.getLogger("agent_api.api")


# ── SSE ownership gate (P0 cross-tenant leak fix — the SSE sibling of the by-id REST check) ──────────
# The live SSE feed `GET /api/meeting/stream` is keyed on a CALLER-SUPPLIED row id (`meeting_id`) and a
# `session_uid`. Row ids are sequential ints, so without an ownership check any authenticated user B could
# `EventSource(...?meeting_id=<A_row>&session_uid=<A_native>)` and stream tenant A's live transcript +
# an ACTIVE, enumerable cross-tenant read. We mirror the WS `/ws` pattern (gateway
# `authorize_subscribe` → `Meeting.user_id == user_id`) and the by-id REST path (`get_transcript_by_id`
# owner-scopes in SQL): verify the caller OWNS the row BEFORE opening the redis stream. Fail CLOSED.
#
# agent-api has no meetings DB; it asks meeting-api `GET /meetings/{meeting_id}` forwarding the
# gateway-injected `X-User-Id` (meeting-api's `_resolve_user_id` trusts it exactly as its by-id path does)
# — a row owned by another user (or absent) returns 404 there → we treat it as NOT-OWNED. The returned
# record's `native_meeting_id` also lets us confirm the requested `session_uid` belongs to the SAME owned
# meeting, so B can't pair its own row with A's native to sniff A's feed. Returns the owned
# meeting record (dict) on success, else None. Injectable so the L2 suite drives it over a fake.
# How room membership was derived, stamped on every room dispatch + log line so an audit can tell
# WHERE the room came from. Under the participant model it is the invite's addresses, resolved to
# subjects here — the ORDER comes from the transcript, the MEMBERSHIP never does.
_ROOM_SOURCE = "invite:participants→admin-api"


def _http_email_subject_lookup(admin_api_url: str, internal_secret: str, admin_token: str):
    """Build the participant ADDRESS → subject resolver: ``(address) -> str | None``.

    THE DOOR PROBLEM, stated because it constrains the whole feature. agent-api already holds an
    internal-tier seam to admin-api (``X-Internal-Secret``, used for the membership index + model
    config), but that tier exposes NO email lookup. The one route that answers this question,
    ``GET /admin/users/email/{email}``, is gated by ``verify_admin_token`` — a DIFFERENT and much
    broader credential (it can also create and patch users). So:

      * the narrow door is tried FIRST — ``GET /internal/users/by-email/{email}`` with the internal
        secret. It does not exist on admin-api today; this is the route that SHOULD be added
        (returning only ``{"id": ...}``), and when it is, the room works with no new credential and
        ``VEXA_ADMIN_API_TOKEN`` can be dropped. A 404 here marks it absent for the process lifetime
        so we probe once, not once per participant;
      * the wide door is used only when an operator has explicitly set ``VEXA_ADMIN_API_TOKEN``;
      * with neither, every lookup returns None — the room resolves to ZERO desks and says so. It
        never falls back to matching a person by name, which is the failure this design exists to
        avoid.

    Fail-CLOSED and quiet-per-call: any error is None (that participant is skipped), never an
    exception that could take down the turn."""
    import urllib.error
    import urllib.parse
    import urllib.request

    base = (admin_api_url or "").rstrip("/")
    state = {"internal_route": True}   # flipped off the first time the narrow door 404s

    def _get(url: str, headers: dict) -> "dict | None":
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:   # noqa: S310 — internal service URL
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode() or "null")

    def _lookup(address: str) -> "str | None":
        if not base or not address:
            return None
        quoted = urllib.parse.quote(str(address).strip().lower(), safe="")
        if state["internal_route"] and internal_secret:
            try:
                row = _get(f"{base}/internal/users/by-email/{quoted}",
                           {"X-Internal-Secret": internal_secret})
                if isinstance(row, dict) and row.get("id") is not None:
                    return str(row["id"])
                return None
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Ambiguous by design: 404 is both "no such user" and "no such route". Probe the
                    # ROUTE once — if the wide door is configured we can tell the difference by
                    # asking it; if it is not, treat the narrow door as present and this address as
                    # unknown (the safe reading — it skips a person rather than inventing one).
                    if not admin_token:
                        return None
                    state["internal_route"] = False
                else:
                    return None
            except Exception:  # noqa: BLE001 — unreachable admin-api → skip this participant
                return None
        if not admin_token:
            logger.warning("room: no email→subject resolver is configured (admin-api has no "
                           "internal by-email route and VEXA_ADMIN_API_TOKEN is unset) — the room "
                           "will mount ZERO desks")
            return None
        try:
            row = _get(f"{base}/admin/users/email/{quoted}", {"X-Admin-API-Key": admin_token})
        except Exception:  # noqa: BLE001 — 404 (no such user) and transport errors alike → skip
            return None
        return str(row["id"]) if isinstance(row, dict) and row.get("id") is not None else None

    return _lookup


def _http_meeting_owner_lookup(meeting_api_url: str, internal_secret: str = ""):
    """Build the default meeting ACCESS lookup: GET {meeting_api_url}/meetings/{id} as the caller.
    Returns a callable ``(user_id, meeting_id, workspaces=None) -> dict | None`` — the meeting record
    the caller may read, or None when the row is absent / not theirs / meeting-api is unreachable
    (fail-closed).

    ``workspaces`` is the caller's OWN memberships, forwarded as ``X-User-Workspaces`` so meeting-api
    can run the third branch of its access union (member of the meeting's bound workspace) instead of
    owner + transcript-share only. This is the grant the SSE route's own comment named as "the clean
    seam" and deliberately left unhonoured: a bot requested inside a workspace makes the WORKSPACE's
    meeting, so a member asking to watch it is not a stranger.

    Omitting the argument keeps the previous, strictly narrower answer — the header is simply absent
    and every existing caller behaves exactly as before. It stays a LOOKUP rather than a local check
    because this function knows neither the row's binding nor the caller's role; meeting-api knows
    both, and one decision made in one place cannot disagree with itself."""
    import urllib.error
    import urllib.request

    base = (meeting_api_url or "").rstrip("/")

    def _lookup(user_id: str, meeting_id: str, workspaces=None) -> "dict | None":
        if not base or not user_id or not str(meeting_id).isdigit():
            return None  # non-numeric row id can't be an owned meeting row → fail closed
        # AS the caller, over the internal tier: meeting-api believes an asserted X-User-Id only from
        # the gateway's signature or from a service presenting X-Internal-Secret.
        headers = {"X-User-Id": str(user_id)}
        if internal_secret:
            headers["X-Internal-Secret"] = internal_secret
        ws = ",".join(str(w).strip() for w in (workspaces or []) if str(w).strip())
        if ws:
            headers["X-User-Workspaces"] = ws
        try:
            req = urllib.request.Request(f"{base}/meetings/{int(meeting_id)}", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError:
            return None   # 404 (not owned / absent) or any other status → refuse
        except Exception:  # noqa: BLE001 — meeting-api unreachable → fail CLOSED, never open the stream
            return None

    return _lookup


def _http_meeting_transcript_lookup(meeting_api_url: str, internal_secret: str = ""):
    """Build the default TRANSCRIPT read: GET {meeting_api_url}/transcripts/by-id/{id} as the caller.
    Returns a callable ``(user_id, meeting_id, workspaces=None) -> list | None`` — the meeting's
    segments, or None when the read did not succeed (absent, not theirs, meeting-api unreachable).

    The same door, identity and access union as `_http_meeting_owner_lookup`: meeting-api decides
    whether this caller may read the words, and a None here is never mistaken for a quiet room."""
    import urllib.error
    import urllib.request

    base = (meeting_api_url or "").rstrip("/")

    def _read(user_id: str, meeting_id: str, workspaces=None) -> "list | None":
        if not base or not user_id or not str(meeting_id).isdigit():
            return None
        headers = {"X-User-Id": str(user_id)}
        if internal_secret:
            headers["X-Internal-Secret"] = internal_secret
        ws = ",".join(str(w).strip() for w in (workspaces or []) if str(w).strip())
        if ws:
            headers["X-User-Workspaces"] = ws
        try:
            req = urllib.request.Request(f"{base}/transcripts/by-id/{int(meeting_id)}",
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    return None
                body = json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError:
            return None
        except Exception:  # noqa: BLE001 — unreachable is a failed read, never an empty one
            return None
        segments = body.get("segments") if isinstance(body, dict) else None
        return list(segments) if isinstance(segments, list) else None

    return _read


def caller_workspaces(roster_root, subject: str) -> list[str]:
    """The workspaces ``subject`` is a member of, READ FROM `policy/members.json` under
    ``roster_root`` — never from a request header (see `meeting_access_check`). Never raises: a scan
    that fails narrows meeting access to owner-only, never opens it."""
    if roster_root is None:
        return []
    try:
        from control_plane.workspace_membership import list_memberships
        return [str(m["workspace_id"]) for m in list_memberships(roster_root, str(subject))
                if m.get("workspace_id")]
    except Exception:  # noqa: BLE001 — fail CLOSED to the owner-only answer
        return []


def meeting_transcript_reader(lookup, roster_root) -> "Callable[[str, object], list | None]":
    """The meeting's words as ``(subject, meeting_id) -> segments | None``, read AS the caller with
    their memberships — the transcript twin of `meeting_access_check`."""
    def _read(subject: str, meeting_id) -> "list | None":
        return lookup(subject, meeting_id, caller_workspaces(roster_root, subject))

    return _read


def meeting_access_check(lookup, roster_root) -> "Callable[[str, object], dict | None]":
    """THE ONE meeting access decision, as a callable ``(subject, meeting_id) -> row | None``.

    Owner, transcript-share recipient, or member of the workspace the meeting is bound to —
    meeting-api evaluates all three; this only says who is asking and which workspaces they belong
    to. The live transcript stream and the chat's meeting grounding both read the same transcript,
    so both ask this one question.

    The caller's workspaces are READ FROM `policy/members.json` under ``roster_root``, not from a
    request header: agent-api is reachable directly in the dev/self-host topology (see
    `subject_of`'s TOPOLOGY BOUNDARY note), where identity headers are spoofable, and this value
    decides who may read a transcript. The membership scan NEVER RAISES — a scan that fails narrows
    access to owner-only, never opens it.

    ``lookup`` is an INJECTED seam: the shipped one takes the caller's workspaces as a third
    argument, and older test fakes take two. The callable is asked which it is, once, rather than
    called three-arg with a `TypeError` rescue — that rescue would also swallow a genuine TypeError
    raised INSIDE the lookup and silently downgrade it to "not authorized"."""
    def _caller_workspaces(subject: str) -> list[str]:
        return caller_workspaces(roster_root, subject)

    try:
        import inspect as _inspect
        takes_workspaces = len(_inspect.signature(lookup).parameters) >= 3
    except (TypeError, ValueError):  # C-implemented or otherwise unintrospectable → narrower call
        takes_workspaces = False

    def _access(subject: str, meeting_id) -> "dict | None":
        if takes_workspaces:
            return lookup(subject, meeting_id, _caller_workspaces(subject))
        return lookup(subject, meeting_id)

    return _access
