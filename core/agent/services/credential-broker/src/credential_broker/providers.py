"""Provider declarations and fixed adapters.

Provider URLs and scopes are reviewed constants, never caller arguments. The OAuth application
(client id and secret) belongs to the operator and arrives from deployment configuration; user
tokens belong in the credential store. The catalog holds the three providers a product caller can
create: Gmail, Google Calendar, and a custom secret.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx

CATALOG = {
    "custom_secret": {"label": "Custom secret", "method": "secret"},
    "google_calendar": {"label": "Google Calendar", "method": "oauth", "family": "google",
                        "scopes": ["https://www.googleapis.com/auth/calendar.events.readonly"]},
    "google_email": {"label": "Gmail", "method": "oauth", "family": "google",
                     "scopes": ["https://www.googleapis.com/auth/gmail.readonly",
                                "https://www.googleapis.com/auth/gmail.compose"]},
}
AUTHORIZE = {"google": "https://accounts.google.com/o/oauth2/v2/auth"}
TOKEN = {"google": "https://oauth2.googleapis.com/token"}
DRAFT_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class ProviderError(Exception):
    """A sanitized user-facing failure; never retains a provider response body."""


def family(provider: str) -> str:
    return CATALOG[provider].get("family", "")


def authorize(provider: str, client: dict, redirect: str, state: str, challenge: str) -> str:
    item = CATALOG[provider]
    args = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(item["scopes"]), "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "select_account consent"}
    return AUTHORIZE[item["family"]] + "?" + urlencode(args)


def tokens(provider: str, client: dict, *, code=None, verifier=None, redirect=None, refresh=None, http=None) -> dict:
    item = CATALOG[provider]
    form = {"client_id": client["client_id"], "client_secret": client["client_secret"],
            "grant_type": "refresh_token" if refresh else "authorization_code"}
    if refresh:
        form["refresh_token"] = refresh
    else:
        form.update(code=code, code_verifier=verifier, redirect_uri=redirect)
    try:
        if http is None:
            with httpx.Client(timeout=15, follow_redirects=False) as session:
                return tokens(provider, client, code=code, verifier=verifier, redirect=redirect,
                              refresh=refresh, http=session)
        response = http.post(TOKEN[item["family"]], data=form)
        if response.status_code != 200:
            raise ProviderError("Authorization failed; reconnect this account")
        result = response.json()
        access = result.get("access_token")
        if not isinstance(access, str) or not access or result.get("token_type", "").lower() != "bearer":
            raise ProviderError("Provider did not return a usable authorization")
        # Providers may omit scope on refresh; initial consent must prove the requested grants.
        required = {s.lower() for s in item["scopes"]}
        granted = {s.lower() for s in result.get("scope", "").split()}
        if not refresh and not required.issubset(granted):
            raise ProviderError("Required permission was not granted; reconnect this account")
        return {"access_token": access, "refresh_token": result.get("refresh_token") or refresh,
                "expires_at": time.time() + max(0, int(result.get("expires_in", 0))),
                "scope": result.get("scope", "")}
    except (httpx.HTTPError, ValueError, TypeError):
        raise ProviderError("Provider authorization is unavailable; try connecting again") from None


def refresh(provider: str, client: dict, value: dict) -> dict:
    result = tokens(provider, client, refresh=value["refresh_token"])
    result["scope"] = result.get("scope") or value.get("scope", "")
    return result


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


_PROVIDER_STATUS = {
    400: "Provider rejected the request arguments; check query, page token and date range",
    401: "Authorization rejected; reconnect this account",
    403: "Provider refused access; check granted scopes and API enablement",
    404: "Requested message or calendar was not found",
    429: "Provider rate limit reached; retry later",
}


def read_account(provider, value, action, *, query="", message_id="", time_min="", time_max="",
                 limit=10, page_token="", http=None):
    """On-demand account reads. Fixed hosts and methods, never a general HTTP proxy."""
    if action not in {"gmail.search", "gmail.read", "gmail.thread", "calendar.events"} or \
            provider != ("google_calendar" if action == "calendar.events" else "google_email"):
        raise ProviderError("Operation does not match this connection")
    if not 1 <= limit <= 20 or len(query) > 500:
        raise ProviderError("Invalid read limits")
    if http is None:
        with httpx.Client(timeout=15, follow_redirects=False) as session:
            return read_account(provider, value, action, query=query, message_id=message_id,
                                time_min=time_min, time_max=time_max, limit=limit,
                                page_token=page_token, http=session)

    def get(url, params):
        try:
            with http.stream("GET", url, params=params,
                             headers={"Authorization": "Bearer " + value["access_token"]}) as response:
                if response.status_code != 200:
                    raise ProviderError(_PROVIDER_STATUS.get(response.status_code,
                                                             "Provider temporarily unavailable; retry later"))
                raw = b""
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > 2_000_000:
                        raise ProviderError("Provider response too large; narrow the request")
                return json.loads(raw)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderError("Account read is unavailable") from None

    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

    def message(mid, full=False, data=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", mid):
            raise ProviderError("Invalid message ID")
        data = data if data is not None else get(base + "/" + mid, {"format": "full" if full else "metadata"})
        payload = data.get("payload", {})
        headers = {h["name"].lower(): h.get("value", "")[:4000] for h in payload.get("headers", [])}
        result = {k: data.get(k) for k in ("id", "threadId", "internalDate", "snippet")}
        result["headers"] = {k: headers.get(k, "") for k in
                             ("from", "to", "cc", "reply-to", "subject", "date", "message-id", "in-reply-to", "references")}
        if full:
            plain, html = [], []

            def parts(part, depth=0):
                if depth > 12 or part.get("filename"):
                    return
                raw = part.get("body", {}).get("data", "")
                if raw and part.get("mimeType") in {"text/plain", "text/html"}:
                    try:
                        text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="replace")
                    except ValueError:
                        text = ""
                    (plain if part["mimeType"] == "text/plain" else html).append(text)
                for child in part.get("parts", []):
                    parts(child, depth + 1)

            parts(payload)
            body = "\n".join(plain)
            if not plain:
                parser = _Text()
                parser.feed("\n".join(html))
                body = "\n".join(parser.text)
            result.update(body=body[:50000], truncated=len(body) > 50000, attachments_included=False)
        return result

    if action == "gmail.thread":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", message_id):
            raise ProviderError("Invalid thread ID")
        data = get("https://gmail.googleapis.com/gmail/v1/users/me/threads/" + message_id, {"format": "full"})
        try:
            offset = int(page_token or "0")
        except ValueError:
            raise ProviderError("Invalid thread offset") from None
        if offset < 0:
            raise ProviderError("Invalid thread offset")
        rows = data.get("messages", [])
        more = offset + limit < len(rows)
        return {"thread_id": data.get("id"), "messages": [message(m["id"], True, m) for m in rows[offset:offset + limit]],
                "has_more": more, "next_page_token": str(offset + limit) if more else "", "total_messages": len(rows)}
    if action == "gmail.read":
        return {"message": message(message_id, True)}
    if action == "gmail.search":
        data = get(base, {"q": query, "maxResults": limit, **({"pageToken": page_token} if page_token else {})})
        return {"messages": [message(m["id"]) for m in data.get("messages", [])[:limit]],
                "has_more": bool(data.get("nextPageToken")), "next_page_token": data.get("nextPageToken", "")}
    try:
        lo = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
        hi = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
        if not lo.tzinfo or not hi.tzinfo or not 0 < (hi - lo).total_seconds() <= 366 * 86400:
            raise ValueError()
    except ValueError:
        raise ProviderError("Use a timezone-qualified time range of at most one year") from None
    data = get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
               {"timeMin": time_min, "timeMax": time_max, "maxResults": limit, "singleEvents": "true",
                "orderBy": "startTime", **({"pageToken": page_token} if page_token else {})})
    keep = ("id", "summary", "description", "start", "end", "location", "htmlLink", "status", "attendees",
            "organizer", "creator", "recurringEventId", "originalStartTime", "updated", "attendeesOmitted")
    return {"events": [{k: e.get(k) for k in keep} for e in data.get("items", [])[:limit]],
            "has_more": bool(data.get("nextPageToken")), "next_page_token": data.get("nextPageToken", "")}


def create_gmail_draft(value, recipient, subject, body, *, http=None):
    from email.message import EmailMessage
    from email.policy import SMTP
    from email.utils import parseaddr

    if DRAFT_SCOPE not in value.get("scope", "").split():
        raise ProviderError("Draft permission required")
    if any(c in recipient + subject for c in "\r\n") or parseaddr(recipient)[1] != recipient or "@" not in recipient:
        raise ProviderError("Use one valid recipient and a single-line subject")
    message = EmailMessage(policy=SMTP)
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    if http is None:
        with httpx.Client(timeout=20, follow_redirects=False) as session:
            return create_gmail_draft(value, recipient, subject, body, http=session)
    try:
        response = http.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                             headers={"Authorization": "Bearer " + value["access_token"]},
                             json={"message": {"raw": raw}})
        if response.status_code not in (200, 201):
            raise ProviderError("Draft creation failed; check account permissions")
        data = response.json()
        if not data.get("id"):
            raise ProviderError("Draft outcome unknown; do not retry automatically")
        return {"draft_id": data["id"], "message_id": data.get("message", {}).get("id"),
                "status": "draft_created", "sent": False}
    except (httpx.HTTPError, ValueError):
        raise ProviderError("Draft outcome unknown; check Gmail Drafts before retrying") from None


def account_email(provider, value, *, http=None):
    if provider != "google_email" or READ_SCOPE not in value.get("scope", "").split():
        return ""
    try:
        if http is None:
            with httpx.Client(timeout=10, follow_redirects=False) as session:
                return account_email(provider, value, http=session)
        response = http.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                            headers={"Authorization": "Bearer " + value["access_token"]})
        if response.status_code != 200:
            return ""
        email = response.json().get("emailAddress", "")
        return email[:320] if isinstance(email, str) else ""
    except (httpx.HTTPError, ValueError, KeyError):
        return ""
