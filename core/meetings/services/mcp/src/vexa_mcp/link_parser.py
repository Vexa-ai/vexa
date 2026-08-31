"""Meeting-URL parsing — ported verbatim from 0.10.6 ``services/mcp/main.py::_parse_meeting_url``.

Pure function (no network): a full meeting URL → platform / native_meeting_id / passcode
(+ the raw URL for legacy Teams enterprise links, + the non-default Teams host for
enterprise short links). Raises HTTPException(422) for unsupported/invalid URLs so the
FastAPI tool route (and the MCP transport on top of it) surfaces a proper error.

Hosts are matched EXACTLY or as a dotted subdomain (``_host_matches``) — never by substring,
which would read ``zoom.us.attacker.example`` as Zoom. meeting-api's parser
(``meeting_api.collector.meeting_link``) carries the same helper; the two are meant to agree.
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
    teams_base_host: Optional[str] = None   # Teams web client this short link is served by
    warnings: List[str] = Field(default_factory=list)


_TEAMS_ENTERPRISE_HOSTS = {
    "teams.microsoft.com",
    "gov.teams.microsoft.us",
    "dod.teams.microsoft.us",
}

# Zoom's meeting domains. Canonical zoom.us (+ every regional subdomain: us02web, us05web, a
# customer's company.zoom.us) and the US-government tenant. Matches the join brick's own rule
# (``modules/join/src/index.ts::resolvePlatform``) — a white-label portal is not inferable from
# its URL, so it is not listed here and never was.
_ZOOM_DOMAINS = ("zoom.us", "zoomgov.com")
# Zoom Events registration portals — recognised only to refuse them with a useful message.
_ZOOM_EVENTS_DOMAINS = ("events.zoom.us", "ev.zoom.com")
# Every domain a hosted platform claims. Used both to match and to recognise a lookalike.
_PLATFORM_DOMAINS = (
    "meet.google.com",
    *_ZOOM_DOMAINS,
    *_ZOOM_EVENTS_DOMAINS,
    "teams.live.com",
    "teams.microsoft.com",
    "teams.microsoft.us",
)


def _host_matches(host: str, *domains: str) -> bool:
    """Exact host, or a subdomain of one of ``domains`` — never a substring match.

    A substring test (``"zoom.us" in host``) also accepts ``zoom.us.attacker.example``: the
    platform name is a *prefix* of an arbitrary domain the submitter controls, so anyone who
    can hand this service a meeting URL chooses the host the bot's browser later opens. The
    registrable domain is the rightmost part of a hostname, so the only sound test is equality
    or a dotted suffix — the leading dot is what makes the suffix test a label boundary rather
    than a substring (``host.endswith("teams.live.com")`` matches ``notteams.live.com``).

    meeting-api's parser (``meeting_api.collector.meeting_link``) carries the same helper; the
    two must agree, since a URL accepted here is handed to ``POST /meetings`` for a second parse.
    """
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_platform_lookalike(host: str) -> bool:
    """True when ``host`` merely CONTAINS a platform's domain without being it or a subdomain
    of it — ``meet.google.com.attacker.example``, ``zoom.us.attacker.example``.

    Such a host is not the platform, and it must not reach the jitsi naming heuristics either:
    ``meet.google.com.attacker.example`` carries a "meet" LABEL, so the self-hosted fallback at
    the bottom of the parser would otherwise adopt it as a jitsi room and hand the bot the same
    submitter-chosen host the exact-match rules just refused. Explicitly declared hosts
    (``VEXA_JITSI_HOSTS``) are unaffected — an operator naming their own deployment is not a guess.
    """
    return any(d in host and not _host_matches(host, d) for d in _PLATFORM_DOMAINS)


def _is_teams_enterprise_host(host: str) -> bool:
    """teams.microsoft.com, the gov/dod tenants, and any tenant subdomain of either.

    Unchanged in reach: these suffix tests already carried the leading dot, so they were always
    label-boundary tests; they are routed through ``_host_matches`` so one rule governs the file.
    """
    return _host_matches(host, *_TEAMS_ENTERPRISE_HOSTS) or host.endswith(".teams.microsoft.us")


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
    if _host_matches(host, "teams.live.com"):
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if not m:
            raise HTTPException(status_code=422, detail="Unsupported teams.live.com URL format. Expected /meet/<10-15 digit id>.")
        native_id = m.group(1)
        passcode = (query.get("p") or [None])[0]
        if not passcode:
            warnings.append("Teams meeting link has no ?p= passcode. Many Teams meetings require it.")
        # The host rides along like every other short-link parse: a personal meeting id addresses
        # a meeting on teams.live.com, and the id alone does not say so. Whoever rebuilds the join
        # URL from (id, passcode) — bot_spawn's construct_meeting_url — would otherwise default to
        # the world-wide enterprise host and send the bot to a different Teams entirely.
        return ParseMeetingLinkResponse(
            platform="teams",
            native_meeting_id=native_id,
            passcode=passcode,
            teams_base_host=host,
            warnings=warnings,
        )

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
    if _host_matches(host, *_ZOOM_EVENTS_DOMAINS):
        raise HTTPException(
            status_code=422,
            detail="Zoom Events links are not supported. Attendees receive unique per-registrant join links via email; these cannot be shared with a bot.",
        )

    # Zoom: zoom.us (all subdomains) and zoomgov.com
    if _host_matches(host, *_ZOOM_DOMAINS):
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
    # A host that merely LOOKS like a hosted platform is excluded from the naming heuristics
    # outright: the branches above already refused it by name, and
    # meet.google.com.attacker.example carries a "meet" label that would otherwise let it back
    # in through this door and hand the bot the host the exact-match rules just rejected.
    explicit_jitsi = host == "meet.jit.si" or host in configured_hosts
    inferred_jitsi = not _is_platform_lookalike(host) and ("jitsi" in host or "meet" in host.split("."))
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
        # A jitsi room name is deployment-scoped: the native id embeds the host for every
        # non-canonical deployment (room@host — jitsi's own XMPP identity shape) so two
        # deployments' same-named rooms never share an identity key. Mirrors meeting-api's
        # parse_meeting_url; meet.jit.si keeps the bare room (canonical, unambiguous).
        return ParseMeetingLinkResponse(
            platform="jitsi",
            native_meeting_id=room if host == "meet.jit.si" else f"{room}@{host}",
            passcode=None,
            meeting_url=url,
            warnings=warnings,
        )

    raise HTTPException(status_code=422, detail="Unsupported meeting URL (unknown provider).")
