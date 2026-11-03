"""Provider declarations and fixed adapters.

Provider URLs and scopes are reviewed constants, never caller arguments. The OAuth application
(client id and secret) belongs to the operator and arrives from deployment configuration; user
tokens belong in the credential store. The catalog holds the three providers a product caller can
create: Gmail, Google Calendar, and a custom secret.

A refusal the person can act on is `ProviderError` (answered 409). Google being unreachable, rate
limiting, or answering with something unusable is `faults.UpstreamFault` (502/503): reconnecting
does not fix an outage, so it is never reported as if it would.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit

import httpx

from .faults import UpstreamFault
# The pieces every adapter shares (`provider_base`), and the calendar-change adapter
# (`calendar_changes`), re-exported: callers and tests address them as `providers.<name>`.
from .provider_base import (CALENDAR_WRITE_SCOPE, DRAFT_SCOPE, READ_SCOPE, ProviderError,  # noqa: F401
                            _NO_ANSWER, _address, _bearer, _fault_for_status)
from .calendar_changes import calendar_event, event_id_for, write_calendar_event  # noqa: F401
from .calendar_sync import calendar_changes  # noqa: F401

CATALOG = {
    "custom_secret": {"label": "Custom secret", "method": "secret"},
    # Reading is the default; changing events is a separate consent the person opts into
    # ("Enable calendar changes"), asked for incrementally on top of the read grant.
    "google_calendar": {"label": "Google Calendar", "method": "oauth", "family": "google",
                        "scopes": ["https://www.googleapis.com/auth/calendar.events.readonly"],
                        "grants": {"calendar_write": ["https://www.googleapis.com/auth/calendar.events"]}},
    "google_email": {"label": "Gmail", "method": "oauth", "family": "google",
                     "scopes": ["https://www.googleapis.com/auth/gmail.readonly",
                                "https://www.googleapis.com/auth/gmail.compose"]},
}
AUTHORIZE = {"google": "https://accounts.google.com/o/oauth2/v2/auth"}
TOKEN = {"google": "https://oauth2.googleapis.com/token"}
#: The permissions a connection row reports, by the scope that grants each.
PERMISSIONS = {"drafts": DRAFT_SCOPE, "calendar_write": CALENDAR_WRITE_SCOPE}


def permissions(provider: str, scope: str) -> list[str]:
    """The optional permissions a stored grant carries, by name (`ConnectionRow.permissions`)."""
    granted = set((scope or "").split())
    wanted = {"google_email": ("drafts",), "google_calendar": ("calendar_write",)}.get(provider, ())
    return [name for name in wanted if PERMISSIONS[name] in granted]


def family(provider: str) -> str:
    return CATALOG[provider].get("family", "")


def scopes_for(provider: str, grant: str = "") -> list[str]:
    """The scopes one consent asks for: the provider's base scopes, plus an opted-in grant's."""
    item = CATALOG[provider]
    extra = item.get("grants", {}).get(grant, []) if grant else []
    if grant and not extra:
        raise ProviderError("This permission does not apply to this connection")
    return item["scopes"] + extra


def authorize(provider: str, client: dict, redirect: str, state: str, challenge: str, grant: str = "") -> str:
    item = CATALOG[provider]
    args = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(scopes_for(provider, grant)), "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "select_account consent"}
    if grant:
        args["include_granted_scopes"] = "true"
    return AUTHORIZE[item["family"]] + "?" + urlencode(args)


def tokens(provider: str, client: dict, *, code=None, verifier=None, redirect=None, refresh=None, http=None,
           grant: str = "") -> dict:
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
                              refresh=refresh, http=session, grant=grant)
        response = http.post(TOKEN[item["family"]], data=form)
        if response.status_code == 429 or response.status_code >= 500:
            raise _fault_for_status(response.status_code, "Provider authorization is unavailable; try again later")
        if response.status_code != 200:
            raise ProviderError("Authorization failed; reconnect this account")
        result = response.json()
        access = result.get("access_token")
        if not isinstance(access, str) or not access or result.get("token_type", "").lower() != "bearer":
            raise UpstreamFault("provider", "bad_answer", "Provider did not return a usable authorization; try again later")
        # Providers may omit scope on refresh; initial consent must prove the requested grants.
        required = {s.lower() for s in scopes_for(provider, grant)}
        granted = {s.lower() for s in result.get("scope", "").split()}
        if not refresh and not required.issubset(granted):
            raise ProviderError("Required permission was not granted; reconnect this account")
        return {"access_token": access, "refresh_token": result.get("refresh_token") or refresh,
                "expires_at": time.time() + max(0, int(result.get("expires_in", 0))),
                "scope": result.get("scope", "")}
    except _NO_ANSWER:
        raise UpstreamFault("provider", "unreachable", "Provider authorization is unavailable; try again later") from None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise UpstreamFault("provider", "bad_answer", "Provider authorization is unavailable; try again later") from None


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


#: The answers that are refusals the person can act on. Any other non-200 answer is a fault.
_PROVIDER_STATUS = {
    400: "Provider rejected the request arguments; check query, page token and date range",
    401: "Authorization rejected; reconnect this account",
    403: "Provider refused access; check granted scopes and API enablement",
    404: "Requested message or calendar was not found",
}


#: Gmail ids are opaque URL-safe tokens; anything else never reaches a link.
_GMAIL_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}")
#: The only hosts a calendar event's `htmlLink` may name to be passed on as `web_url`.
CALENDAR_LINK_HOSTS = frozenset({"calendar.google.com", "www.google.com"})


def gmail_web_url(account: str, thread_id, message_id=None) -> str:
    """The Gmail web address of a thread in the CONNECTED mailbox, or "" when none can be built.

    `account` is the address the broker recorded at consent (Gmail's own profile answer), never a
    caller argument. The account is chosen with Google's `authuser=<address>` parameter, which the
    signed-in browser resolves to that session. Two forms are NOT used: an index (`/u/0/`) opens
    whichever account the browser lists first, and `/mail/u/<address>/` is a path Gmail answers with
    its own "Temporary Error (404) … your account is temporarily unavailable" page (2026-10-13: every
    link on a page the agent wrote failed that way while the same person's Inbox tab worked). Without
    a recorded address no link is built at all.

    The fragment is `#all/<thread id>` (the message id only when Gmail gave no thread id): `all`
    finds the thread whatever its labels. The address is percent-encoded, keeping only `@`."""
    if not isinstance(account, str) or not 3 <= len(account) <= 320 or account.count("@") != 1 \
            or any(ord(c) <= 32 or ord(c) == 127 for c in account):
        return ""
    target = thread_id if isinstance(thread_id, str) and thread_id else message_id
    if not isinstance(target, str) or not _GMAIL_ID.fullmatch(target):
        return ""
    return "https://mail.google.com/mail/?authuser=" + quote(account, safe="@") + "#all/" + target


def calendar_web_url(link) -> str:
    """An event's Google `htmlLink`, passed through only as an https URL on a Google Calendar host."""
    if not isinstance(link, str) or len(link) > 2048:
        return ""
    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.hostname not in CALENDAR_LINK_HOSTS or port is not None \
            or parts.username is not None or parts.password is not None or parts.netloc != parts.hostname:
        return ""
    return link


def read_account(provider, value, action, *, query="", message_id="", time_min="", time_max="",
                 limit=10, page_token="", account="", http=None):
    """On-demand account reads. Fixed hosts and methods, never a general HTTP proxy.

    `account` is the connected address from the broker's own metadata; it only shapes `web_url`."""
    if action not in {"gmail.search", "gmail.read", "gmail.thread", "calendar.events"} or \
            provider != ("google_calendar" if action == "calendar.events" else "google_email"):
        raise ProviderError("Operation does not match this connection")
    if not 1 <= limit <= 20 or len(query) > 500:
        raise ProviderError("Invalid read limits")
    if http is None:
        with httpx.Client(timeout=15, follow_redirects=False) as session:
            return read_account(provider, value, action, query=query, message_id=message_id,
                                time_min=time_min, time_max=time_max, limit=limit,
                                page_token=page_token, account=account, http=session)

    def get(url, params):
        try:
            with http.stream("GET", url, params=params,
                             headers={"Authorization": "Bearer " + value["access_token"]}) as response:
                if response.status_code in _PROVIDER_STATUS:
                    raise ProviderError(_PROVIDER_STATUS[response.status_code])
                if response.status_code == 429:
                    raise _fault_for_status(429, "Provider rate limit reached; retry later")
                if response.status_code != 200:
                    raise _fault_for_status(response.status_code, "Provider temporarily unavailable; retry later")
                raw = b""
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > 2_000_000:
                        raise ProviderError("Provider response too large; narrow the request")
                return json.loads(raw)
        except _NO_ANSWER:
            raise UpstreamFault("provider", "unreachable", "Account read is unavailable; the provider could not be reached") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise UpstreamFault("provider", "bad_answer", "Account read is unavailable; the provider answered with an unreadable response") from None

    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

    def message(mid, full=False, data=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", mid):
            raise ProviderError("Invalid message ID")
        data = data if data is not None else get(base + "/" + mid, {"format": "full" if full else "metadata"})
        payload = data.get("payload", {})
        headers = {h["name"].lower(): h.get("value", "")[:4000] for h in payload.get("headers", [])}
        result = {k: data.get(k) for k in ("id", "threadId", "internalDate", "snippet")}
        # Gmail's own labels (DRAFT, SENT, INBOX, ...): an unsent draft must never read as mail that went out.
        labels = [x for x in (data.get("labelIds") or []) if isinstance(x, str)][:50]
        result["labels"] = labels
        result["is_draft"] = "DRAFT" in labels
        link = gmail_web_url(account, result.get("threadId"), result.get("id"))
        if link:
            result["web_url"] = link
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
        for row in rows:  # a thread's messages link to the thread they were read from
            if isinstance(row, dict) and not row.get("threadId"):
                row["threadId"] = data.get("id")
        more = offset + limit < len(rows)
        page = [message(m["id"], True, m) for m in rows[offset:offset + limit]]
        drafts = _thread_drafts(get, [m["id"] for m in page if m["is_draft"]])
        for m in page:
            if m["id"] in drafts:
                m["draft_id"] = drafts[m["id"]]
        return {"thread_id": data.get("id"), "messages": page,
                "drafts": [{"draft_id": drafts[m["id"]], "message_id": m["id"]} for m in page if m["id"] in drafts],
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

    def event(e):
        row = {k: e.get(k) for k in keep}
        link = calendar_web_url(e.get("htmlLink"))
        if link:
            row["web_url"] = link
        # THE CALL'S LINK (founder 2026-10-16): a Zoom added through Calendar's add-on lives only in
        # conferenceData — derived once here so "join this call" needs no pasted link
        from credential_broker.calendar_sync import video_link  # noqa: PLC0415
        url = video_link(e)
        if url:
            row["meeting_url"] = url
        return row
    return {"events": [event(e) for e in data.get("items", [])[:limit]],
            "has_more": bool(data.get("nextPageToken")), "next_page_token": data.get("nextPageToken", "")}


def _thread_drafts(get, message_ids) -> dict:
    """Draft id by message id for the draft messages of one thread page. Gmail names a draft by a
    draft id distinct from its message id, and only the drafts listing maps one to the other; it is
    read only when the page holds a draft, and for a bounded number of pages."""
    wanted, found, token = set(message_ids), {}, ""
    for _ in range(5):
        if not wanted - set(found):
            break
        data = get("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                   {"maxResults": 500, **({"pageToken": token} if token else {})})
        for d in data.get("drafts") or []:
            mid = (d.get("message") or {}).get("id")
            if mid in wanted and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", str(d.get("id", ""))):
                found[mid] = d["id"]
        token = data.get("nextPageToken") or ""
        if not token:
            break
    return found


def reply_context(value, thread_id: str, in_reply_to: str = "", *, http) -> dict:
    """What a reply in ``thread_id`` needs: the replied-to message's Message-ID, References and
    Subject. ``in_reply_to`` names that message (a Gmail message id from a read); empty means the
    thread's last message. A message outside the thread is refused."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", thread_id) or \
            (in_reply_to and not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", in_reply_to)):
        raise ProviderError("Invalid thread or message ID")
    params = [("format", "metadata"), ("metadataHeaders", "Message-ID"),
              ("metadataHeaders", "References"), ("metadataHeaders", "Subject")]
    base = "https://gmail.googleapis.com/gmail/v1/users/me/"
    try:
        if in_reply_to:
            response = http.get(base + "messages/" + in_reply_to, params=params, headers=_bearer(value))
        else:
            response = http.get(base + "threads/" + thread_id, params=params, headers=_bearer(value))
        if response.status_code == 404:
            raise ProviderError("Thread or message not found; use ids from a Gmail read")
        if response.status_code in (400, 401, 403):
            raise ProviderError("Thread could not be read; check account permissions")
        if response.status_code != 200:
            raise _fault_for_status(response.status_code, "Thread could not be read; retry later")
        data = response.json()
        message = data if in_reply_to else (data.get("messages") or [None])[-1]
        if not message or message.get("threadId", thread_id if not in_reply_to else None) != thread_id:
            raise ProviderError("That message is not in this thread")
        headers = {h["name"].lower(): h.get("value", "") for h in message.get("payload", {}).get("headers", [])}
    except _NO_ANSWER:
        raise UpstreamFault("provider", "unreachable", "Thread could not be read; retry later") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise UpstreamFault("provider", "bad_answer", "Thread could not be read; retry later") from None
    mid = headers.get("message-id", "").strip()
    if any(c in mid for c in "\r\n") or not re.fullmatch(r"<[^<>\s]{1,900}>", mid):
        raise ProviderError("The replied-to message has no usable Message-ID")
    refs = " ".join(r for r in headers.get("references", "").split() if re.fullmatch(r"<[^<>\s]{1,900}>", r))
    subject = headers.get("subject", "").replace("\r", " ").replace("\n", " ").strip()
    return {"thread_id": thread_id, "message_id": mid, "references": (refs + " " + mid).strip()[-8000:],
            "subject": subject}


def draft_message(recipient, subject, body, *, cc=(), bcc=(), reply=None, attachments=()) -> str:
    """The draft as Gmail's base64url ``raw``. A reply carries In-Reply-To, References and a "Re:"
    subject so Gmail files it in the conversation. Attachments are (filename, content_type, bytes)."""
    from email.message import EmailMessage
    from email.policy import SMTP

    if any(c in subject for c in "\r\n"):
        raise ProviderError("Use one valid recipient and a single-line subject")
    message = EmailMessage(policy=SMTP)
    message["To"] = _address(recipient)
    if cc:
        message["Cc"] = ", ".join(_address(a) for a in cc)
    if bcc:
        message["Bcc"] = ", ".join(_address(a) for a in bcc)
    if reply:
        if not subject.strip():
            subject = reply["subject"]
        if not re.match(r"(?i)^\s*re:", subject):
            subject = "Re: " + subject
        message["In-Reply-To"] = reply["message_id"]
        message["References"] = reply["references"]
    message["Subject"] = subject
    message.set_content(body)
    for filename, content_type, data in attachments:
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _draft_call(http, method, url, value, payload, *, created: bool) -> dict:
    """POST (create) or PUT (update) a draft; never the send endpoint."""
    unknown = "Draft outcome unknown; check Gmail Drafts before retrying"
    try:
        response = http.request(method, url, headers=_bearer(value), json=payload)
        if response.status_code == 404 and not created:
            raise ProviderError("Draft not found in Gmail; it may have been sent or deleted")
        if response.status_code in (400, 401, 403):
            raise ProviderError("Draft creation failed; check account permissions")
        if response.status_code not in (200, 201):
            raise _fault_for_status(response.status_code, unknown)
        data = response.json()
        if not data.get("id"):
            raise UpstreamFault("provider", "bad_answer", "Draft outcome unknown; do not retry automatically")
        message = data.get("message", {}) or {}
        out = {"draft_id": data["id"], "message_id": message.get("id"),
               "status": "draft_created" if created else "draft_updated", "sent": False}
        if message.get("threadId"):
            out["thread_id"] = message["threadId"]
        return out
    except _NO_ANSWER:
        raise UpstreamFault("provider", "unreachable", unknown) from None
    except (httpx.HTTPError, ValueError, AttributeError):
        raise UpstreamFault("provider", "bad_answer", unknown) from None


def create_gmail_draft(value, recipient, subject, body, *, cc=(), bcc=(), thread_id="", in_reply_to="",
                       attachments=(), draft_id="", http=None):
    """Create (or, with ``draft_id``, replace) an unsent Gmail draft. Never sends."""
    if DRAFT_SCOPE not in value.get("scope", "").split():
        raise ProviderError("Draft permission required")
    if http is None:
        with httpx.Client(timeout=20, follow_redirects=False) as session:
            return create_gmail_draft(value, recipient, subject, body, cc=cc, bcc=bcc, thread_id=thread_id,
                                      in_reply_to=in_reply_to, attachments=attachments, draft_id=draft_id,
                                      http=session)
    if any(c in recipient + subject for c in "\r\n"):
        raise ProviderError("Use one valid recipient and a single-line subject")
    reply = reply_context(value, thread_id, in_reply_to, http=http) if thread_id else None
    raw = draft_message(recipient, subject, body, cc=cc, bcc=bcc, reply=reply, attachments=attachments)
    message = {"raw": raw, **({"threadId": thread_id} if reply else {})}
    base = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    if draft_id:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", draft_id):
            raise ProviderError("Invalid draft ID")
        return _draft_call(http, "PUT", base + "/" + draft_id, value, {"id": draft_id, "message": message},
                           created=False)
    return _draft_call(http, "POST", base, value, {"message": message}, created=True)


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
