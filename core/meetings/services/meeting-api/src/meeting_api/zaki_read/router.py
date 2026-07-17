"""HTTP adapter from the native meeting store to the sealed ``zaki-read.v1`` profile."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Callable

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import JSONResponse

from ..collector.ports import TranscriptStore


INDEX_LIMIT = 200
DEFAULT_INDEX_LIMIT = 50
ITEM_CONTENT_CAP_BYTES = 256 * 1024
RESPONSE_CAP_BYTES = 270_336
ZAKI_NOTETAKER_NAME = "ZAKI Notetaker"
SEALED_MEETING_PLATFORMS = frozenset({"google_meet", "teams", "zoom", "jitsi"})


def _valid_read_token(token: str | None) -> bool:
    return bool(
        token
        and 32 <= len(token) <= 512
        and token == token.strip(" ")
        and all(0x20 <= ord(character) <= 0x7E for character in token)
    )


def _error(status: int, code: str, message: str, request_id: str | None = None) -> JSONResponse:
    headers = {"cache-control": "no-store"}
    if request_id:
        headers["x-request-id"] = request_id
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}, "truncated": False},
        headers=headers,
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _bounded_limit(value: str | None, *, maximum: int, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except ValueError:
        return fallback
    if parsed < 1:
        return fallback
    return min(parsed, maximum)


def _controls_digest(controls: dict) -> str:
    encoded = json.dumps(controls, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _encode_cursor(*, route: str, user_id: int, offset: int, controls: dict, snapshot: datetime, token: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "route": route,
            "user": user_id,
            "offset": offset,
            "controls": _controls_digest(controls),
            "snapshot": snapshot.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(token.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode()


def _decode_cursor(value: str, *, route: str, user_id: int, controls: dict, token: str) -> tuple[int, datetime] | None:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.new(token.encode(), payload, hashlib.sha256).digest()
        decoded = json.loads(payload)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    snapshot = _parse_time(decoded.get("snapshot")) if isinstance(decoded, dict) else None
    if (
        len(payload) == 0
        or not hmac.compare_digest(signature, expected)
        or not isinstance(decoded, dict)
        or decoded.get("v") != 1
        or decoded.get("route") != route
        or decoded.get("user") != user_id
        or decoded.get("controls") != _controls_digest(controls)
        or not isinstance(decoded.get("offset"), int)
        or decoded["offset"] < 0
        or snapshot is None
    ):
        return None
    return decoded["offset"], snapshot


def _title(meeting: dict) -> str:
    data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
    for candidate in (data.get("title"), data.get("name")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:500]
    return f"Meeting {meeting['id']}"


def _capture_notice(data: dict, now: datetime) -> dict | None:
    capture = data.get("zaki_capture")
    read_scope = data.get("zaki_read")
    if not isinstance(capture, dict) or not isinstance(read_scope, dict):
        return None
    attested_at = _parse_time(capture.get("tenant_attested_at"))
    policy_version = capture.get("tenant_policy_version")
    if (
        read_scope.get("enabled") is not True
        or capture.get("state") != "authorized"
        or capture.get("bot_name") != ZAKI_NOTETAKER_NAME
        or capture.get("tenant_attested") is not True
        or attested_at is None
        or attested_at > now
        or not isinstance(policy_version, str)
        or not policy_version.strip()
        or len(policy_version.strip()) > 80
    ):
        return None
    return {
        "bot_visible": True,
        "tenant_attested_at": attested_at.isoformat(),
        "policy_version": policy_version.strip(),
    }


async def _read_scope_disabled(store: TranscriptStore, user_id: int) -> bool:
    """Distinguish an explicit tenant opt-out from malformed or absent evidence."""
    meetings = await store.list_meetings(user_id)
    states = {
        read_scope.get("enabled")
        for meeting in meetings
        if meeting.get("user_id") == user_id
        and isinstance((data := meeting.get("data")), dict)
        and isinstance((read_scope := data.get("zaki_read")), dict)
        and isinstance(read_scope.get("enabled"), bool)
    }
    return states == {False}


def _retention(data: dict, scope: str, now: datetime) -> dict | None:
    policy = data.get("zaki_retention")
    if not isinstance(policy, dict) or policy.get("state") != "open":
        return None
    expired_scopes = policy.get("expired_scopes")
    if not isinstance(expired_scopes, list) or scope in expired_scopes:
        return None
    expiries = policy.get("scope_expiries")
    if not isinstance(expiries, dict):
        return None
    expires_at = _parse_time(expiries.get(scope))
    if expires_at is None or expires_at <= now:
        return None
    return {"scope": f"minutes.{scope}", "expires_at": expires_at.isoformat()}


def _summary(data: dict) -> tuple[str, str | None] | None:
    value = data.get("summary")
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            updated = value.get("updated_at")
            return text.strip(), updated if isinstance(updated, str) else None
    summaries = data.get("summaries")
    if isinstance(summaries, list):
        for candidate in summaries:
            if isinstance(candidate, dict) and isinstance(candidate.get("text"), str) and candidate["text"].strip():
                updated = candidate.get("updated_at")
                return candidate["text"].strip(), updated if isinstance(updated, str) else None
    return None


def _item_parts(item_id: str) -> tuple[str, int] | None:
    kind, separator, raw_id = item_id.partition(":")
    if separator != ":" or kind not in {"meeting", "transcript", "summary"}:
        return None
    if not raw_id.isdecimal() or int(raw_id) <= 0:
        return None
    return kind, int(raw_id)


def _turns(transcript: dict) -> list[dict] | None:
    segments = transcript.get("segments")
    if not isinstance(segments, list) or not segments or len(segments) > 4096:
        return None
    meeting_start = _parse_time(transcript.get("start_time"))
    turns: list[dict] = []
    previous_start: datetime | None = None
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        text = segment.get("text")
        speaker = segment.get("speaker")
        if not isinstance(text, str) or not text.strip() or len(text) > 65_536:
            return None
        if not isinstance(speaker, str) or not speaker.strip():
            speaker = "Unknown speaker"
        elif len(speaker) > 200:
            return None
        started_at = _parse_time(segment.get("absolute_start_time"))
        ended_at = _parse_time(segment.get("absolute_end_time"))
        if started_at is None and meeting_start is not None:
            try:
                started_at = meeting_start + timedelta(seconds=float(segment.get("start")))
            except (TypeError, ValueError):
                return None
        if ended_at is None and meeting_start is not None:
            try:
                ended_at = meeting_start + timedelta(seconds=float(segment.get("end")))
            except (TypeError, ValueError):
                ended_at = started_at
        if (
            started_at is None
            or (ended_at is not None and ended_at < started_at)
            or (previous_start is not None and started_at < previous_start)
        ):
            return None
        previous_start = started_at
        turn = {
            "speaker": speaker.strip(),
            "started_at": started_at.isoformat(),
            "text": text.strip(),
        }
        if ended_at is not None:
            turn["ended_at"] = ended_at.isoformat()
        turns.append(turn)
    return turns


def _content_size(content: dict) -> int:
    return len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode())


def _response_size(body: dict) -> int:
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())


async def _item_from_store(
    store: TranscriptStore,
    *,
    user_id: int,
    item_id: str,
    variant: str,
    now: datetime,
) -> dict | None:
    parsed = _item_parts(item_id)
    row_id = parsed[1] if parsed is not None else 0
    transcript = await store.get_transcript_by_id(user_id, row_id)
    meetings = await store.list_meetings(user_id)
    meeting = next(
        (
            candidate
            for candidate in meetings
            if candidate.get("id") == row_id and candidate.get("user_id") == user_id
        ),
        None,
    )
    if parsed is None or transcript is None or meeting is None:
        return None
    kind, _ = parsed
    data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
    notice = _capture_notice(data, now)
    occurred_at = meeting.get("start_time") or meeting.get("created_at")
    updated_at = meeting.get("updated_at") or occurred_at
    occurred_time = _parse_time(occurred_at)
    if (
        notice is None
        or occurred_time is None
        or occurred_time > now
        or _parse_time(updated_at) is None
    ):
        return None
    meeting_id = f"meeting:{row_id}"
    base = {
        "id": item_id,
        "kind": kind,
        "title": _title(meeting),
        "occurred_at": occurred_at,
        "updated_at": updated_at,
        "sensitivity": "sensitive_pii",
    }
    if kind == "meeting":
        retention = _retention(data, "transcript", now)
        ended_at = meeting.get("end_time")
        attendees = data.get("attendees", [])
        platform = meeting.get("platform")
        if (
            retention is None
            or not isinstance(platform, str)
            or platform not in SEALED_MEETING_PLATFORMS
            or _parse_time(ended_at) is None
            or not isinstance(attendees, list)
            or len(attendees) > 1000
            or any(not isinstance(attendee, str) or not attendee.strip() for attendee in attendees)
        ):
            return None
        content = {
            "platform": platform,
            "started_at": occurred_at,
            "ended_at": ended_at,
            "attendees": [attendee.strip()[:500] for attendee in attendees],
        }
        return {**base, "capture_notice": notice, "retention": retention, "content": content}
    if kind == "transcript":
        retention = _retention(data, "transcript", now)
        turns = _turns(transcript)
        if retention is None or turns is None:
            return None
        summary = _summary(data)
        if variant == "summary" and summary is not None:
            content = {"format": "summary", "text": summary[0]}
        else:
            languages = {
                segment.get("language")
                for segment in transcript.get("segments", [])
                if isinstance(segment, dict) and isinstance(segment.get("language"), str)
            }
            content = {
                "format": "speaker_turns",
                **({"language": next(iter(languages))} if len(languages) == 1 else {}),
                "turns": turns,
            }
        return {
            **base,
            "title": f"{base['title']} transcript"[:500],
            "meeting_id": meeting_id,
            "capture_notice": notice,
            "retention": retention,
            "content": content,
        }
    summary = _summary(data)
    retention = _retention(data, "summary", now)
    summary_updated_at = (
        _parse_time(summary[1] or updated_at) if summary is not None else None
    )
    if summary is None or retention is None or summary_updated_at is None:
        return None
    return {
        **base,
        "title": f"{base['title']} summary"[:500],
        "meeting_id": meeting_id,
        "updated_at": summary_updated_at.isoformat(),
        "retention": retention,
        "content": {"format": "summary", "text": summary[0]},
    }


def _matches(query: str | None, *values: object) -> bool:
    if query is None:
        return True
    haystack = "\n".join(
        json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        for value in values
    ).casefold()
    return query in haystack


async def _project_items(
    store: TranscriptStore,
    user_id: int,
    now: datetime,
    *,
    query: str | None = None,
) -> list[dict]:
    meetings = await store.list_meetings(user_id)
    items: list[dict] = []
    for meeting in meetings:
        if meeting.get("user_id") != user_id:
            continue
        data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
        notice = _capture_notice(data, now)
        transcript_retention = _retention(data, "transcript", now)
        occurred_at = meeting.get("start_time") or meeting.get("created_at")
        updated_at = meeting.get("updated_at") or occurred_at
        occurred_time = _parse_time(occurred_at)
        if (
            notice is None
            or transcript_retention is None
            or occurred_time is None
            or occurred_time > now
            or _parse_time(updated_at) is None
        ):
            continue
        base = {
            "title": _title(meeting),
            "occurred_at": occurred_at,
            "updated_at": updated_at,
            "sensitivity": "sensitive_pii",
        }
        meeting_id = f"meeting:{meeting['id']}"
        if (
            _parse_time(meeting.get("end_time")) is not None
            and _matches(query, base["title"], meeting.get("platform"), data.get("attendees", []))
        ):
            items.append({
                **base,
                "id": meeting_id,
                "kind": "meeting",
                "retention": transcript_retention,
            })
        transcript = await store.get_transcript_by_id(user_id, meeting["id"])
        turns = _turns(transcript) if isinstance(transcript, dict) else None
        if (
            turns is not None
            and _matches(query, base["title"], turns)
        ):
            items.append({
                **base,
                "id": f"transcript:{meeting['id']}",
                "kind": "transcript",
                "title": f"{base['title']} transcript"[:500],
                "meeting_id": meeting_id,
                "retention": transcript_retention,
            })
        summary = _summary(data)
        summary_retention = _retention(data, "summary", now)
        summary_updated_at = (
            _parse_time(summary[1] or updated_at) if summary is not None else None
        )
        if (
            summary is not None
            and summary_retention is not None
            and summary_updated_at is not None
            and _matches(query, base["title"], summary[0])
        ):
            items.append({
                **base,
                "id": f"summary:{meeting['id']}",
                "kind": "summary",
                "title": f"{base['title']} summary"[:500],
                "meeting_id": meeting_id,
                "updated_at": summary_updated_at.isoformat(),
                "retention": summary_retention,
            })
    return sorted(items, key=lambda item: (item["updated_at"], item["id"]), reverse=True)


def build_router(
    store: TranscriptStore,
    *,
    token: str | None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> APIRouter:
    router = APIRouter(prefix="/api/zaki/read/v1")
    configured_token = token if _valid_read_token(token) else None

    @router.get("/{user_id}/index")
    async def index(
        response: Response,
        user_id: str,
        limit: str | None = Query(None),
        cursor: str | None = Query(None),
        since: str | None = Query(None),
        x_zaki_read_token: str | None = Header(None),
        x_zaki_user_id: str | None = Header(None),
        x_request_id: str | None = Header(None),
    ):
        response.headers["cache-control"] = "no-store"
        if x_request_id:
            response.headers["x-request-id"] = x_request_id
        if not configured_token or not x_zaki_read_token or not secrets.compare_digest(x_zaki_read_token, configured_token):
            return _error(401, "bad_token", "Read token was rejected", x_request_id)
        if x_zaki_user_id != user_id or not user_id.isdecimal() or int(user_id) <= 0:
            return _error(404, "unknown_user", "User was not found", x_request_id)
        bounded_limit = _bounded_limit(
            limit,
            maximum=INDEX_LIMIT,
            fallback=DEFAULT_INDEX_LIMIT,
        )
        numeric_user_id = int(user_id)
        if await _read_scope_disabled(store, numeric_user_id):
            return _error(403, "scope_disabled", "Minutes read access is disabled", x_request_id)
        since_at = _parse_time(since) if since is not None else None
        if since is not None and since_at is None:
            return _error(400, "bad_since", "since must be ISO-8601", x_request_id)
        controls = {"limit": bounded_limit, "since": since_at.isoformat() if since_at else None}
        evaluated_at = now().astimezone(timezone.utc)
        offset = 0
        snapshot = evaluated_at
        if cursor:
            decoded = _decode_cursor(
                cursor,
                route="index",
                user_id=numeric_user_id,
                controls=controls,
                token=configured_token,
            )
            if decoded is None:
                return _error(400, "bad_cursor", "cursor is invalid", x_request_id)
            offset, snapshot = decoded
        items = [
            item
            for item in await _project_items(store, numeric_user_id, evaluated_at)
            if _parse_time(item["updated_at"]) <= snapshot
            and (since_at is None or _parse_time(item["updated_at"]) >= since_at)
        ]
        page = items[offset:offset + bounded_limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(items)
        return {
            "items": page,
            "truncated": truncated,
            **({
                "next_cursor": _encode_cursor(
                    route="index",
                    user_id=numeric_user_id,
                    offset=next_offset,
                    controls=controls,
                    snapshot=snapshot,
                    token=configured_token,
                )
            } if truncated else {}),
        }

    @router.get("/{user_id}/item/{item_id}")
    async def item(
        response: Response,
        user_id: str,
        item_id: str,
        variant: str = Query("full"),
        x_zaki_read_token: str | None = Header(None),
        x_zaki_user_id: str | None = Header(None),
        x_request_id: str | None = Header(None),
    ):
        response.headers["cache-control"] = "no-store"
        if x_request_id:
            response.headers["x-request-id"] = x_request_id
        if not configured_token or not x_zaki_read_token or not secrets.compare_digest(x_zaki_read_token, configured_token):
            return _error(401, "bad_token", "Read token was rejected", x_request_id)
        if x_zaki_user_id != user_id or not user_id.isdecimal() or int(user_id) <= 0:
            return _error(404, "unknown_user", "User was not found", x_request_id)
        if variant not in {"full", "summary"}:
            return _error(400, "bad_variant", "variant must be full or summary", x_request_id)
        if await _read_scope_disabled(store, int(user_id)):
            return _error(403, "scope_disabled", "Minutes read access is disabled", x_request_id)
        projected = await _item_from_store(
            store,
            user_id=int(user_id),
            item_id=item_id,
            variant=variant,
            now=now().astimezone(timezone.utc),
        )
        if projected is None:
            return _error(404, "unknown_item", "Item was not found", x_request_id)
        if _content_size(projected["content"]) > ITEM_CONTENT_CAP_BYTES:
            return _error(413, "item_too_large", "Item exceeds the read cap", x_request_id)
        body = {"item": projected, "truncated": False}
        if _response_size(body) > RESPONSE_CAP_BYTES:
            return _error(413, "item_too_large", "Item exceeds the read cap", x_request_id)
        return body

    @router.get("/{user_id}/search")
    async def search(
        response: Response,
        user_id: str,
        q: str = Query(""),
        limit: str | None = Query(None),
        cursor: str | None = Query(None),
        x_zaki_read_token: str | None = Header(None),
        x_zaki_user_id: str | None = Header(None),
        x_request_id: str | None = Header(None),
    ):
        response.headers["cache-control"] = "no-store"
        if x_request_id:
            response.headers["x-request-id"] = x_request_id
        if not configured_token or not x_zaki_read_token or not secrets.compare_digest(x_zaki_read_token, configured_token):
            return _error(401, "bad_token", "Read token was rejected", x_request_id)
        if x_zaki_user_id != user_id or not user_id.isdecimal() or int(user_id) <= 0:
            return _error(404, "unknown_user", "User was not found", x_request_id)
        query = q.strip().casefold()
        if not query:
            return _error(400, "bad_query", "q is required", x_request_id)
        bounded_limit = _bounded_limit(limit, maximum=50, fallback=20)
        numeric_user_id = int(user_id)
        if await _read_scope_disabled(store, numeric_user_id):
            return _error(403, "scope_disabled", "Minutes read access is disabled", x_request_id)
        controls = {"limit": bounded_limit, "query": query}
        evaluated_at = now().astimezone(timezone.utc)
        offset = 0
        snapshot = evaluated_at
        if cursor:
            decoded = _decode_cursor(
                cursor,
                route="search",
                user_id=numeric_user_id,
                controls=controls,
                token=configured_token,
            )
            if decoded is None:
                return _error(400, "bad_cursor", "cursor is invalid", x_request_id)
            offset, snapshot = decoded
        items = [
            item
            for item in await _project_items(store, numeric_user_id, evaluated_at, query=query)
            if _parse_time(item["updated_at"]) <= snapshot
        ]
        page = items[offset:offset + bounded_limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(items)
        return {
            "items": page,
            "truncated": truncated,
            **({
                "next_cursor": _encode_cursor(
                    route="search",
                    user_id=numeric_user_id,
                    offset=next_offset,
                    controls=controls,
                    snapshot=snapshot,
                    token=configured_token,
                )
            } if truncated else {}),
        }

    @router.api_route(
        "/{user_id}/{read_path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def reject_mutation(
        user_id: str,
        read_path: str,
        x_request_id: str | None = Header(None),
    ):
        del user_id, read_path
        return _error(405, "read_only", "Only GET is allowed on the read plane", x_request_id)

    return router
