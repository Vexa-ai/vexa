"""Meeting-URL parsing — ported verbatim from 0.10.6 ``services/mcp/main.py::_parse_meeting_url``.

Pure function (no network): a full meeting URL → platform / native_meeting_id / passcode
(+ the raw URL for legacy Teams enterprise links, + the non-default Teams host for
enterprise short links). Raises HTTPException(422) for unsupported/invalid URLs so the
FastAPI tool route (and the MCP transport on top of it) surfaces a proper error.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field


class ParseMeetingLinkResponse(BaseModel):
    platform: str
    native_meeting_id: str
    passcode: Optional[str] = None
    meeting_url: Optional[str] = None       # raw URL for long Teams /l/meetup-join/ links
    teams_base_host: Optional[str] = None   # non-default Teams host (e.g. teams.microsoft.com)
    warnings: List[str] = Field(default_factory=list)


_TEAMS_ENTERPRISE_HOSTS = {
    "teams.microsoft.com",
    "gov.teams.microsoft.us",
    "dod.teams.microsoft.us",
}


def _is_teams_enterprise_host(host: str) -> bool:
    return (
        host in _TEAMS_ENTERPRISE_HOSTS
        or host.endswith(".teams.microsoft.us")
        or host.endswith(".teams.microsoft.com")
    )


def parse_meeting_url(meeting_url: str) -> ParseMeetingLinkResponse:
    url = (meeting_url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="meeting_url cannot be empty")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query or "")

    warnings: List[str] = []

    # Google Meet
    if host == "meet.google.com":
        # Block /lookup/ paths — internal Google URLs, not directly joinable
        if path.startswith("/lookup/"):
            raise HTTPException(
                status_code=422,
                detail="Google Meet /lookup/ URLs cannot be joined directly. Use the standard meeting link from your calendar invite.",
            )
        code = path.strip("/").split("/")[0] if path else ""
        # Standard abc-defg-hij format
        if re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", code):
            return ParseMeetingLinkResponse(platform="google_meet", native_meeting_id=code, passcode=None, warnings=warnings)
        # Custom Workspace nickname: 5-40 lowercase alphanumeric + hyphens
        if re.fullmatch(r"^[a-z0-9][a-z0-9-]{3,38}[a-z0-9]$", code):
            warnings.append("Custom Google Meet nickname URL detected. This works for Google Workspace accounts only.")
            return ParseMeetingLinkResponse(platform="google_meet", native_meeting_id=code, passcode=None, warnings=warnings)
        raise HTTPException(
            status_code=422,
            detail="Invalid Google Meet URL: expected https://meet.google.com/abc-defg-hij or a custom Workspace nickname.",
        )

    # Teams personal (teams.live.com/meet/<digits>?p=<passcode>)
    if host.endswith("teams.live.com"):
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if not m:
            raise HTTPException(status_code=422, detail="Unsupported teams.live.com URL format. Expected /meet/<10-15 digit id>.")
        native_id = m.group(1)
        passcode = (query.get("p") or [None])[0]
        if not passcode:
            warnings.append("Teams meeting link has no ?p= passcode. Many Teams meetings require it.")
        return ParseMeetingLinkResponse(platform="teams", native_meeting_id=native_id, passcode=passcode, warnings=warnings)

    # Teams enterprise: teams.microsoft.com, gov.teams.microsoft.us, dod.teams.microsoft.us, etc.
    if _is_teams_enterprise_host(host):
        # Deep link format: /v2/?meetingjoin=true#/meet/<id>?p=<passcode>
        # The meeting info lives in the fragment, not the path/query
        fragment = parsed.fragment or ""
        if path.rstrip("/") in ("/v2", "") and fragment.startswith("/meet/"):
            frag_parsed = urlparse("https://x" + fragment)
            fm = re.match(r"^/meet/(\d{10,15})/?$", frag_parsed.path)
            if fm:
                native_id = fm.group(1)
                frag_query = parse_qs(frag_parsed.query or "")
                passcode = (frag_query.get("p") or [None])[0]
                if not passcode:
                    warnings.append("Teams meeting link has no ?p= passcode. Many Teams meetings require it.")
                return ParseMeetingLinkResponse(
                    platform="teams",
                    native_meeting_id=native_id,
                    passcode=passcode,
                    teams_base_host=host,
                    warnings=warnings,
                )

        # Short new-style URL: /meet/<numeric_id>?p=<passcode>
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if m:
            native_id = m.group(1)
            passcode = (query.get("p") or [None])[0]
            if not passcode:
                warnings.append("Teams meeting link has no ?p= passcode. Many Teams meetings require it.")
            return ParseMeetingLinkResponse(
                platform="teams",
                native_meeting_id=native_id,
                passcode=passcode,
                teams_base_host=host,
                warnings=warnings,
            )
        # Long legacy URL: /l/meetup-join/...
        if "/l/meetup-join/" in path:
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            warnings.append(
                "Legacy Teams enterprise URL detected. The full URL will be passed directly to the bot. "
                "The meeting ID shown is a stable hash of the URL used for deduplication."
            )
            return ParseMeetingLinkResponse(
                platform="teams",
                native_meeting_id=url_hash,
                passcode=None,
                meeting_url=url,
                warnings=warnings,
            )
        raise HTTPException(
            status_code=422,
            detail="Unsupported Teams enterprise URL format. Expected /meet/<id>?p=<passcode> or /l/meetup-join/...",
        )

    # Zoom Events — not joinable via shareable URL (check before general zoom.us match)
    if host in {"events.zoom.us", "ev.zoom.com"} or host.endswith(".events.zoom.us"):
        raise HTTPException(
            status_code=422,
            detail="Zoom Events links are not supported. Attendees receive unique per-registrant join links via email; these cannot be shared with a bot.",
        )

    # Zoom: zoom.us (all subdomains) and zoomgov.com
    if "zoom.us" in host or "zoomgov.com" in host:
        parts = [p for p in path.split("/") if p]
        native_id = ""
        if len(parts) >= 2 and parts[0] in {"j", "w"}:
            native_id = parts[1]
        elif len(parts) >= 3 and parts[0] == "wc" and parts[1] == "join":
            native_id = parts[2]
        elif len(parts) >= 2 and parts[0] == "my":
            raise HTTPException(
                status_code=422,
                detail="Zoom personal meeting room links (/my/...) are not supported. Ask the host to share a direct meeting link (/j/<id>).",
            )
        # Relax to 9-11 digits (Zoom supports 9, 10, and 11 digit IDs)
        if not re.fullmatch(r"^\d{9,11}$", native_id or ""):
            raise HTTPException(
                status_code=422,
                detail="Unsupported Zoom URL format. Expected https://zoom.us/j/<9-11 digit id>.",
            )
        passcode = (query.get("pwd") or [None])[0]
        return ParseMeetingLinkResponse(platform="zoom", native_meeting_id=native_id, passcode=passcode, warnings=warnings)

    # Jitsi Meet — the canonical public deployment, VEXA_JITSI_HOSTS-declared deployments
    # (the SAME setting meeting-api's parser honours), and the self-hosted naming conventions:
    # a host containing "jitsi" (jitsi.example.org) or a "meet" hostname label anywhere
    # (meet.example.org, eu.meet.example.org — jitsi's recommended naming, regionalized).
    # Checked LAST so every known provider above claims its hosts first. The room is the path's
    # single URL-safe segment (the id round-trips into path params, so whitespace is invalid);
    # the bot receives the full URL so it always lands on the right deployment.
    configured_hosts = {
        h.strip().lower() for h in os.getenv("VEXA_JITSI_HOSTS", "").split(",") if h.strip()
    }
    explicit_jitsi = host == "meet.jit.si" or host in configured_hosts
    inferred_jitsi = "jitsi" in host or "meet" in host.split(".")
    if explicit_jitsi or inferred_jitsi:
        room = path.strip("/")
        if not room or not re.fullmatch(r"[^/?#\s]+", room):
            raise HTTPException(
                status_code=422,
                detail="Unsupported Jitsi URL format. Expected https://<jitsi-host>/<RoomName>.",
            )
        if not explicit_jitsi:
            warnings.append(
                "Host inferred as a self-hosted Jitsi deployment from its name. If this is not a "
                "Jitsi meeting the bot will fail to join; declare the host in VEXA_JITSI_HOSTS to "
                "silence this warning."
            )
        return ParseMeetingLinkResponse(
            platform="jitsi",
            native_meeting_id=room,
            passcode=None,
            meeting_url=url,
            warnings=warnings,
        )

    raise HTTPException(status_code=422, detail="Unsupported meeting URL (unknown provider).")
