"""Microsoft Graph mail transport — client-credentials, application permissions.

For a pilot that runs M365 with IMAP switched off (the common bank posture), Graph is the only
way in. This wiring keeps the four transport contracts (see `mail_transport.py`) exactly:

  C1 cursor — `receivedDateTime gt <ISO>` by default (a per-message, mid-batch-resumable
     position), or Graph's own `delta` link when `VEXA_GRAPH_USE_DELTA=1`.
  C2 threading — outbound goes out as a DRAFT-then-send so Graph hands back the real
     `internetMessageId` to register in `mail_thread`; inbound `internetMessageHeaders` are
     surfaced so `In-Reply-To` routing is unchanged.
  C3 ICS — attachments are fetched and base64-decoded. Exchange ALSO delivers invites as
     `eventMessageRequest` items with the calendar data lifted out of MIME into properties;
     those are synthesized back into an ICS (see `_synthesize_ics`).
  C4 no sleeping — `fetch()` walks `@odata.nextLink` and stops.

Env (all required for `graph`):
    VEXA_GRAPH_TENANT_ID · VEXA_GRAPH_CLIENT_ID · VEXA_GRAPH_CLIENT_SECRET · VEXA_GRAPH_MAILBOX
Optional: VEXA_GRAPH_BASE · VEXA_GRAPH_LOGIN · VEXA_GRAPH_USE_DELTA · VEXA_GRAPH_PAGE_SIZE

Azure app registration needs APPLICATION permissions `Mail.ReadWrite` + `Mail.Send` with admin
consent, and should be scoped to the single mailbox with an ApplicationAccessPolicy.

urllib only — same idiom as `flows_steps/common.py:http()`. The HTTP layer is injectable so the
whole transport is exercised offline against a fake.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

from flows_integrations.ics import decode_ics
from flows_integrations.mail_transport import ICS_TYPES, InboundMessage, MailTransport

SELECT = ("id,receivedDateTime,internetMessageId,subject,from,body,hasAttachments,"
          "internetMessageHeaders")
# eventMessage-only fields: harmless on a plain message, load-bearing on an Exchange invite.
EVENT_SELECT = "meetingMessageType,startDateTime,endDateTime,location"


def _urllib_http(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method, data=data)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class GraphTransport(MailTransport):
    name = "graph"

    def __init__(self, *, http=None, env: dict | None = None):
        e = env if env is not None else os.environ
        self.tenant = e.get("VEXA_GRAPH_TENANT_ID", "")
        self.client_id = e.get("VEXA_GRAPH_CLIENT_ID", "")
        self.client_secret = e.get("VEXA_GRAPH_CLIENT_SECRET", "")
        self.mailbox = e.get("VEXA_GRAPH_MAILBOX", "")
        self.base = e.get("VEXA_GRAPH_BASE", "https://graph.microsoft.com/v1.0").rstrip("/")
        self.login = e.get("VEXA_GRAPH_LOGIN", "https://login.microsoftonline.com").rstrip("/")
        self.use_delta = e.get("VEXA_GRAPH_USE_DELTA", "0") == "1"
        self.page_size = int(e.get("VEXA_GRAPH_PAGE_SIZE", "25"))
        self._http = http or _urllib_http
        self._token = ""
        self._token_exp = 0.0
        missing = [k for k, v in [("VEXA_GRAPH_TENANT_ID", self.tenant),
                                  ("VEXA_GRAPH_CLIENT_ID", self.client_id),
                                  ("VEXA_GRAPH_CLIENT_SECRET", self.client_secret),
                                  ("VEXA_GRAPH_MAILBOX", self.mailbox)] if not v]
        if missing:
            raise ValueError("VEXA_MAIL_TRANSPORT=graph needs " + ", ".join(missing))

    # ---------------------------------------------------------------- auth + request plumbing
    def token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        body = urllib.parse.urlencode({
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default"}).encode()
        code, raw = self._http("POST", f"{self.login}/{self.tenant}/oauth2/v2.0/token",
                               {"content-type": "application/x-www-form-urlencoded"}, body)
        if code != 200:
            raise RuntimeError(f"graph token {code}: {raw[:200]}")
        tok = json.loads(raw)
        self._token = tok["access_token"]
        # 60 s of slack: a token that expires mid-page is a 401 loop, and we never sleep.
        self._token_exp = time.time() + float(tok.get("expires_in", 3600)) - 60
        return self._token

    def _call(self, method: str, url: str, body: dict | None = None,
              extra: dict | None = None) -> tuple[int, object]:
        if not url.startswith("http"):
            url = f"{self.base}{url}"
        headers = {"Authorization": f"Bearer {self.token()}",
                   "content-type": "application/json", **(extra or {})}
        code, raw = self._http(method, url, headers,
                               json.dumps(body).encode() if body is not None else None)
        if not raw.strip():
            return code, {}
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw

    def _get(self, url: str) -> dict:
        # Prefer plain-text bodies: Exchange stores HTML, and every consumer downstream
        # (strip_quotes, the agent's turn text) wants text.
        code, body = self._call("GET", url, extra={"Prefer": 'outlook.body-content-type="text"'})
        if code != 200 or not isinstance(body, dict):
            raise RuntimeError(f"graph GET {code}: {str(body)[:200]}")
        return body

    def _mb(self) -> str:
        return f"/users/{urllib.parse.quote(self.mailbox)}"

    # ------------------------------------------------------------------------------ inbound
    def address(self) -> str:
        return self.mailbox.lower()

    def tail_cursor(self) -> str:
        """First boot anchors at NOW (timestamp mode) or at an empty delta (delta mode) — either
        way the existing mailbox is never replayed (C1)."""
        if self.use_delta:
            url = f"{self._mb()}/mailFolders/inbox/messages/delta?$select=id"
            while True:
                page = self._get(url)
                if page.get("@odata.deltaLink"):
                    return page["@odata.deltaLink"]
                nxt = page.get("@odata.nextLink")
                if not nxt:
                    return ""
                url = nxt
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _first_url(self, cursor: str) -> str:
        if self.use_delta and str(cursor).startswith("http"):
            return cursor
        since = cursor or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        q = urllib.parse.urlencode({
            "$filter": f"receivedDateTime gt {since}",
            "$orderby": "receivedDateTime asc",
            "$top": str(self.page_size),
            "$select": SELECT + "," + EVENT_SELECT})
        return f"{self._mb()}/mailFolders/inbox/messages?{q}"

    def fetch(self, cursor: str) -> Iterator[InboundMessage]:
        url, pending = self._first_url(cursor), []
        delta_next = ""
        while url:
            page = self._get(url)
            pending.extend(v for v in page.get("value", []) if isinstance(v, dict))
            delta_next = page.get("@odata.deltaLink") or delta_next
            url = page.get("@odata.nextLink") or ""
        for i, m in enumerate(pending):
            if m.get("@removed") or not m.get("id"):
                continue                       # delta reports deletions — not inbound mail
            if self.use_delta:
                # A delta link is only valid once the whole page-set is drained, so every message
                # but the last carries the OLD cursor. A crash mid-batch therefore re-delivers —
                # which is safe by construction: admission dedups on the Message-ID (C1).
                pos = delta_next if (i == len(pending) - 1 and delta_next) else cursor
            else:
                pos = m.get("receivedDateTime") or cursor
            yield self._to_inbound(m, str(pos))

    def _to_inbound(self, m: dict, cursor: str) -> InboundMessage:
        headers = {h.get("name", ""): h.get("value", "")
                   for h in (m.get("internetMessageHeaders") or []) if isinstance(h, dict)}
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        body = ((m.get("body") or {}).get("content") or "")
        ics = self._read_ics(m)
        return InboundMessage(cursor=cursor,
                              message_id=(m.get("internetMessageId") or m.get("id") or "").strip(),
                              frm=frm, subject=m.get("subject") or "",
                              headers=headers, body=body, ics=ics)

    def _read_ics(self, m: dict) -> str | None:
        if m.get("hasAttachments"):
            code, att = self._call("GET", f"{self._mb()}/messages/{m['id']}/attachments")
            if code == 200 and isinstance(att, dict):
                for a in att.get("value", []):
                    name = (a.get("name") or "").lower()
                    ctype = (a.get("contentType") or "").split(";")[0].strip().lower()
                    if ctype in ICS_TYPES or name.endswith(".ics"):
                        raw = a.get("contentBytes") or ""
                        try:
                            return decode_ics(base64.b64decode(raw))
                        except Exception:  # noqa: BLE001 — a malformed attachment is not an invite
                            return None
        return _synthesize_ics(m)

    # ------------------------------------------------------------------------------ outbound
    def _draft_and_send(self, message: dict) -> str:
        """Create a draft, read back the REAL internetMessageId, then send it.

        `sendMail` would be one call but returns 202 with no body — and the Message-ID is the
        whole threading contract (C2): `mail_thread` keys on it, and their reply's In-Reply-To
        is how the answer finds its conversation."""
        code, made = self._call("POST", f"{self._mb()}/messages", message)
        if code >= 400 and message.get("internetMessageHeaders"):
            # Some tenants reject In-Reply-To/References as reserved headers. Threading still
            # holds without them: what routes the reply is OUR Message-ID, which their client
            # echoes back — the header only makes the thread look right in their client.
            stripped = {k: v for k, v in message.items() if k != "internetMessageHeaders"}
            code, made = self._call("POST", f"{self._mb()}/messages", stripped)
        if code >= 400 or not isinstance(made, dict) or not made.get("id"):
            raise RuntimeError(f"graph draft {code}: {str(made)[:200]}")
        code, err = self._call("POST", f"{self._mb()}/messages/{made['id']}/send")
        if code >= 400:
            raise RuntimeError(f"graph send {code}: {str(err)[:200]}")
        return (made.get("internetMessageId") or "").strip()

    def send(self, to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> str:
        msg = {"subject": subject,
               "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": to}}]}
        if in_reply_to:
            msg["internetMessageHeaders"] = [{"name": "In-Reply-To", "value": in_reply_to},
                                             {"name": "References", "value": in_reply_to}]
        return self._draft_and_send(msg)

    def send_calendar_reply(self, to: str, subject: str, text: str, ics: str) -> str:
        """iMIP RSVP over Graph: the ICS rides as a `text/calendar; method=REPLY` attachment.

        On an Exchange-native mailbox the first-class path is `POST /events/{id}/accept`, which
        needs the event id rather than the invite mail — a follow-up once a live tenant exists.
        The iMIP attachment is what an internet-standard organizer (Google, Zimbra) reads."""
        return self._draft_and_send({
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "invite.ics",
                "contentType": 'text/calendar; method=REPLY; charset="UTF-8"',
                "contentBytes": base64.b64encode(ics.encode()).decode()}]})


def _esc(v: str) -> str:
    """ICS is line-oriented; an embedded newline would end the property mid-value."""
    return v.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "")


def _synthesize_ics(m: dict) -> str | None:
    """Exchange strips iMIP: a meeting request often arrives as `eventMessageRequest` with the
    calendar data lifted out of MIME into message PROPERTIES and no `.ics` part at all. Rebuild
    the minimum VEVENT the parser needs.

    Shape per the Graph `eventMessage` resource (meetingMessageType · startDateTime ·
    endDateTime · location, each a DateTimeTimeZone whose `timeZone` may be a WINDOWS name —
    `ics.resolve_tzid` handles that). ⚠ Written from the documented resource shape and exercised
    against fixtures only; it has not yet met a live Exchange tenant."""
    if (m.get("meetingMessageType") or "") != "meetingRequest":
        return None
    st = m.get("startDateTime") or {}
    dt = (st.get("dateTime") or "")[:19].replace("-", "").replace(":", "")
    if len(dt) != 15:
        return None
    tz = st.get("timeZone") or ""
    tzpart = "" if tz.upper() == "UTC" else f';TZID="{tz}"'
    stamp = "Z" if tz.upper() == "UTC" else ""
    where = ((m.get("location") or {}).get("displayName") or "")
    body = ((m.get("body") or {}).get("content") or "")
    organizer = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
    # UID: the invite's own iCalUId is not exposed on the message, so admission dedups on the
    # internetMessageId instead — same guarantee (one admission per delivered request), and a
    # re-sent update arrives as a distinct message anyway.
    uid = (m.get("internetMessageId") or m.get("id") or "").strip().strip("<>")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "PRODID:-//Vexa//graph-transport//EN", "VERSION:2.0", "METHOD:REQUEST",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTART{tzpart}:{dt}{stamp}",
        f"ORGANIZER:mailto:{organizer}", f"SUMMARY:{_esc(m.get('subject') or 'Meeting')}",
        f"LOCATION:{_esc(where)}", f"DESCRIPTION:{_esc(body[:900])}",
        "END:VEVENT", "END:VCALENDAR", ""])
