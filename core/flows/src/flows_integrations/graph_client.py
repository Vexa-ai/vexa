"""MICROSOFT GRAPH — the mailbox client, client-credentials, application permissions.

A deployment that runs Microsoft 365 with IMAP switched off (the common bank posture, and the
posture of the central-bank pilot this exists for) has no other way in. This module is the wire:
token, request plumbing, message listing, attachment reads, and draft-then-send. It knows nothing
about admission, routing or flows — `flows_integrations/graph_inbox.py` is the inbox seam over
it, `flows_steps/notify.py`'s Graph channel is the send seam over it, and both are testable
because the HTTP layer is injected.

Ported from PR Vexa-ai/vexa#1318, which targeted an abandoned base and merged nowhere.

Configuration (all four, together — the `mailbox_graph` capability in `config.v1.json`):

    VEXA_GRAPH_TENANT_ID · VEXA_GRAPH_CLIENT_ID · VEXA_GRAPH_CLIENT_SECRET · VEXA_GRAPH_MAILBOX

Azure app registration needs APPLICATION permissions `Mail.ReadWrite` + `Mail.Send` with admin
consent, and MUST be scoped to the single mailbox with an Exchange ApplicationAccessPolicy —
without that policy the credential reads every mailbox in the tenant.

⚠ **NO LIVE TENANT HAS EVER ANSWERED THIS CODE.** There is no M365 credential in the vault; every
call below is exercised against a fake HTTP layer and against fixtures built from documented
Graph shapes and one captured real invitation. That is the rung, and it is stated here rather
than in a commit message because the next person to touch this needs it.

urllib only — the same idiom as `flows_steps/common.py:http()`.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import flows_config

# `hasAttachments` is what decides whether we spend a second request; the eventMessage fields are
# harmless on a plain message and load-bearing on an Exchange-delivered invite.
SELECT = ("id,receivedDateTime,internetMessageId,subject,from,body,hasAttachments,"
          "internetMessageHeaders")
EVENT_SELECT = "meetingMessageType,startDateTime,endDateTime,location"

ICS_TYPES = ("text/calendar", "application/ics")

GRAPH_KEYS = ("VEXA_GRAPH_TENANT_ID", "VEXA_GRAPH_CLIENT_ID", "VEXA_GRAPH_CLIENT_SECRET",
              "VEXA_GRAPH_MAILBOX")


def urllib_http(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method, data=data)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class GraphClient:
    """One mailbox, over Graph. Construct once per process; the token is cached and refreshed."""

    def __init__(self, *, http=None, env=None):
        get = (lambda k: (env or {}).get(k, "")) if env is not None else flows_config.get
        self.tenant = get("VEXA_GRAPH_TENANT_ID") or ""
        self.client_id = get("VEXA_GRAPH_CLIENT_ID") or ""
        self.client_secret = get("VEXA_GRAPH_CLIENT_SECRET") or ""
        self.mailbox = (get("VEXA_GRAPH_MAILBOX") or "").strip()
        self.base = (get("VEXA_GRAPH_BASE") or "https://graph.microsoft.com/v1.0").rstrip("/")
        self.login = (get("VEXA_GRAPH_LOGIN") or "https://login.microsoftonline.com").rstrip("/")
        self.page_size = int(get("VEXA_GRAPH_PAGE_SIZE") or 25)
        self._http = http or urllib_http
        self._token = ""
        self._token_exp = 0.0
        # THE REFUSAL NAMES THE KEYS, NEVER THE VALUES — same shape as `emailx.creds` and
        # `require_admin_key`. The config contract declares these four as one `mailbox_graph`
        # capability (mode=all), so three-of-four is `misconfigured` at /health as well as a
        # refusal here; it cannot be `required-explicit`, because that would refuse the boot of
        # every deployment that is not on Microsoft. See `src/config.v1.json`.
        missing = [k for k, v in zip(GRAPH_KEYS, (self.tenant, self.client_id,
                                                  self.client_secret, self.mailbox)) if not v]
        if missing:
            raise flows_config.ConfigError(
                "the Microsoft Graph mailbox needs " + ", ".join(missing)
                + " — set all four, or select another inbox with VEXA_MAIL_INBOX.")

    # ── auth + request plumbing ──────────────────────────────────────────────────────────────
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
        # 60 s of slack: a token that expires mid-page is a 401 loop, and nothing here sleeps.
        self._token_exp = time.time() + float(tok.get("expires_in", 3600)) - 60
        return self._token

    def call(self, method: str, url: str, body: dict | None = None,
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

    def get(self, url: str) -> dict:
        # Prefer plain-text bodies: Exchange stores HTML, and every consumer downstream
        # (strip_quotes, the agent's turn text) wants text.
        code, body = self.call("GET", url, extra={"Prefer": 'outlook.body-content-type="text"'})
        if code != 200 or not isinstance(body, dict):
            raise RuntimeError(f"graph GET {code}: {str(body)[:200]}")
        return body

    def mb(self) -> str:
        return f"/users/{urllib.parse.quote(self.mailbox)}"

    def address(self) -> str:
        return self.mailbox.lower()

    # ── inbound ──────────────────────────────────────────────────────────────────────────────
    def messages_since(self, since_iso: str) -> list[dict]:
        """Inbox messages with `receivedDateTime ge <since_iso>`, oldest first, all pages.

        **`ge`, not `gt`, and the caller de-duplicates.** Graph's timestamp has second
        granularity in practice and two invitations can share one — `gt` silently drops the
        second of them, forever, with no error anywhere. The seam this plugs into already solved
        exactly that for mailpit (a watermark plus a `mail_seen` id set), so the position is
        overlapping-by-design and idempotence comes from the id set, not from the filter.

        No sleeping, no retry loop (C4): walks `@odata.nextLink` and stops."""
        q = urllib.parse.urlencode({
            "$filter": f"receivedDateTime ge {since_iso}",
            "$orderby": "receivedDateTime asc",
            "$top": str(self.page_size),
            "$select": SELECT + "," + EVENT_SELECT})
        url = f"{self.mb()}/mailFolders/inbox/messages?{q}"
        out: list[dict] = []
        while url:
            page = self.get(url)
            out.extend(v for v in page.get("value", []) if isinstance(v, dict))
            url = page.get("@odata.nextLink") or ""
        return out

    def newest_received(self) -> str:
        """The newest message's `receivedDateTime`, or now — the first-boot anchor (C1)."""
        q = urllib.parse.urlencode({"$top": "1", "$orderby": "receivedDateTime desc",
                                    "$select": "receivedDateTime"})
        page = self.get(f"{self.mb()}/mailFolders/inbox/messages?{q}")
        for m in (page.get("value") or []):
            got = (m or {}).get("receivedDateTime")
            if got:
                return got
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def ics_attachment(self, message_id: str) -> str | None:
        """The `text/calendar` attachment of one message, decoded — or None (C3)."""
        code, att = self.call("GET", f"{self.mb()}/messages/{message_id}/attachments")
        if code != 200 or not isinstance(att, dict):
            return None
        from flows_integrations.outlook import decode_ics
        for a in att.get("value", []):
            name = (a.get("name") or "").lower()
            ctype = (a.get("contentType") or "").split(";")[0].strip().lower()
            if ctype in ICS_TYPES or name.endswith(".ics"):
                try:
                    return decode_ics(base64.b64decode(a.get("contentBytes") or ""))
                except Exception:  # noqa: BLE001 — a malformed attachment is not an invite
                    return None
        return None

    # ── outbound ─────────────────────────────────────────────────────────────────────────────
    def draft_and_send(self, message: dict) -> str:
        """Create a draft, read back the REAL `internetMessageId`, then send it.

        `sendMail` would be one call, but it answers 202 with no body — and the Message-ID is the
        entire threading contract (C2): `mail_thread` keys on it, and the reply's `In-Reply-To`
        is how an answer finds its conversation. A send whose id we never learned is a
        conversation we can never route.

        Some tenants reject `In-Reply-To`/`References` as reserved headers. The retry drops them
        and sends anyway: threading still holds, because what routes the reply is OUR Message-ID
        echoed back by their client — the header only makes the thread look right in theirs."""
        code, made = self.call("POST", f"{self.mb()}/messages", message)
        if code >= 400 and message.get("internetMessageHeaders"):
            stripped = {k: v for k, v in message.items() if k != "internetMessageHeaders"}
            code, made = self.call("POST", f"{self.mb()}/messages", stripped)
        if code >= 400 or not isinstance(made, dict) or not made.get("id"):
            raise RuntimeError(f"graph draft {code}: {str(made)[:200]}")
        code, err = self.call("POST", f"{self.mb()}/messages/{made['id']}/send")
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
        return self.draft_and_send(msg)

    def send_calendar_reply(self, to: str, subject: str, text: str, ics: str) -> str:
        """iMIP RSVP over Graph — the ICS rides as a `text/calendar; method=REPLY` attachment.

        On an Exchange-native mailbox the first-class path is `POST /events/{id}/accept`, which
        needs the event id rather than the invite mail; that is a follow-up once a live tenant
        exists. The iMIP attachment is what an internet-standard organizer (Google, Zimbra)
        reads, and it is the same bytes the SMTP path attaches."""
        return self.draft_and_send({
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "invite.ics",
                "contentType": 'text/calendar; method=REPLY; charset="UTF-8"',
                "contentBytes": base64.b64encode(ics.encode()).decode()}]})


_CLIENT: GraphClient | None = None


def client() -> GraphClient:
    """The process's Graph client — one token cache, shared by the inbox and the send channel."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = GraphClient()
    return _CLIENT


def use(c: GraphClient | None) -> None:
    """Install a client (fixtures, the storm). None restores the env-built default."""
    global _CLIENT
    _CLIENT = c
