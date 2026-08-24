#!/usr/bin/env python3
"""m365_rig — the Microsoft 365 half of the witness ladder, hands-free.

`mail_real.py` is the Gmail/IMAP world: a human types a calendar invite into their own
client and the flows mailbox watches it arrive. That is unrepeatable — it needs a human,
a browser and a Google account. This rig is the same three facts over Microsoft Graph,
driven entirely from a terminal, so the intake path can be exercised on every run:

  meeting create   → a REAL Teams meeting (joinWebUrl + the (platform, native_id) the
                     meeting-api gateway expects). Proves OnlineMeetings.ReadWrite.All +
                     the Teams ApplicationAccessPolicy are actually in force.
  invite send      → a REAL calendar event with isOnlineMeeting=true and attendees, so
                     Exchange composes and delivers a REAL invitation with a REAL ICS.
                     This is the GENERATOR for the flows intake path — the thing a
                     customer's employee does when they invite the Vexa mailbox.
  mail poll        → reads the tenant mailbox over Graph and reports what landed:
                     subject, from, and — for each ICS — WHICH properties carry the
                     Teams join URL. The live counterpart of the Graph mail transport.
  mail sent-ics    → pulls the raw MIME of the SENT copy and reports the same ICS shape.
                     This is a source of ICS BYTES, never a delivery proof (see below).
  gmail poll       → the delivery proof: polls an EXTERNAL mailbox we control
                     (vexa-mail vault, IMAP) for the invitation Exchange delivered.
  cleanup          → cancels/deletes everything the rig created. The tenant is a shared
                     asset; a rig that leaves residue is a rig nobody runs twice.

FINDING — Exchange suppresses self-delivery. Inviting the organizer's OWN mailbox
produces no inbox message: Exchange recognises the organizer as the attendee, writes the
event straight into their calendar and delivers nothing. `mail poll` on the organizer's
inbox will therefore be empty no matter how long you wait, and that emptiness is NOT a
bug in the transport. Send to a DIFFERENT mailbox (`gmail poll`) when you need to prove
delivery; use `mail sent-ics` when you only need the bytes Microsoft composed.

Credentials come from the SOPS vault (`~/dev/vexa-secrets/business/m365-graph.enc.env`)
into process env only — never printed, never written to disk, exactly as mail_real.py
does it. Everything the rig creates is appended to a local ledger so `cleanup` can find
it again after the process is gone.

Run `python3 witness/m365_rig.py --help` for the verb table.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

VAULT = os.path.expanduser("~/dev/vexa-secrets/business/m365-graph.enc.env")
GRAPH = "https://graph.microsoft.com/v1.0"
LEDGER = Path(__file__).resolve().parent / ".m365-rig-ledger.jsonl"

REQUIRED = ("VEXA_GRAPH_TENANT_ID", "VEXA_GRAPH_CLIENT_ID", "VEXA_GRAPH_CLIENT_SECRET",
            "VEXA_GRAPH_ORGANIZER", "VEXA_GRAPH_ORGANIZER_ID")


def say(m: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')} · {m}", flush=True)


# ── pure helpers (no I/O — unit-tested offline in tests/test_m365_ics.py) ─────────────

def unfold_ics(text: str) -> str:
    """RFC 5545 line unfolding.

    THE sharp edge of reading a Microsoft ICS: Exchange folds every line at 75 octets,
    and a Teams join URL is ~200 characters — so it arrives split across three physical
    lines, each continuation starting with a single space or tab. A regex for
    `https://teams.microsoft.com/...` run over the RAW text matches only the first
    fragment and yields a truncated, unjoinable URL. Unfold FIRST, always."""
    return re.sub(r"\r?\n[ \t]", "", text or "")


# Properties an Exchange-composed ICS may carry a Teams join URL in. Order is the order
# a parser should prefer them: the X- property is unambiguous and machine-written; the
# others are human-facing text that also happens to contain the link.
TEAMS_URL_PROPERTIES = (
    "X-MICROSOFT-SKYPETEAMSMEETINGURL",
    "X-MICROSOFT-ONLINEMEETINGEXTERNALLINK",
    "X-MICROSOFT-ONLINEMEETINGCONFLINK",
    "LOCATION",
    "DESCRIPTION",
    "X-ALT-DESC",
)

_TEAMS_URL = re.compile(r"https://teams\.(?:microsoft|live)\.com/[^\s<>\"'>;\\]+", re.I)
_TEAMS_THREAD = re.compile(r"19:meeting_[^@%\s/]+@thread\.v2", re.I)
_TEAMS_SHORT = re.compile(r"/meet/([^/?#]+)", re.I)


def ics_teams_properties(ics: str) -> dict[str, str]:
    """Map ICS property name → the Teams join URL found in it (unfolded).

    Reports what Microsoft ACTUALLY populated for this event, which is the input a
    calendar parser has to be written against. Only properties inside the VEVENT block
    are considered — a VTIMEZONE has no join links, and scanning the whole file is how
    a parser ends up matching the wrong block (the flows engine already learned that
    with DTSTART, see mailbox.parse_ics)."""
    if not ics or "BEGIN:VEVENT" not in ics:
        return {}
    ve = unfold_ics(ics).split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    found: dict[str, str] = {}
    for line in ve.splitlines():
        name = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
        if name not in TEAMS_URL_PROPERTIES:
            continue
        # ICS escapes commas/semicolons in TEXT values; a URL in DESCRIPTION arrives as
        # `https://teams.microsoft.com/l/meetup-join/19%3ameeting_...` with literal \,
        # and the DESCRIPTION's newlines arrive as a literal backslash-n — which is why
        # the URL character class excludes `\`: without that the match runs straight past
        # the end of the link into the next line's prose ("…?p=Hsp…\nMeeting").
        m = _TEAMS_URL.search(line.replace("\\,", ",").replace("\\;", ";"))
        if m and name not in found:
            found[name] = m.group(0).rstrip(">).,;")
    return found


def ics_teams_evidence(ics: str) -> dict[str, Any]:
    """Everything an Exchange-composed VEVENT says about its Teams meeting.

    Measured against a live Microsoft 365 tenant on 2026-08-24 (see README-m365.md). The
    two facts a parser must be built around:

    * **LOCATION never holds the URL.** It is the literal string "Microsoft Teams
      Meeting"; so is X-MICROSOFT-LOCATIONS' DisplayName, whose LocationUri is "".
      A parser that reads LOCATION finds nothing and reports "no meeting link".
    * **DESCRIPTION holds TWO DIFFERENT LINKS and the first one is the wrong one.**
      Microsoft now writes the short form `teams.microsoft.com/meet/<digits>?p=<passcode>`
      on the "Join:" line and the canonical `/l/meetup-join/19%3ameeting_…%40thread.v2`
      further down after "System reference:". A first-match regex over DESCRIPTION yields
      the short id (e.g. 373241627805208) — NOT the thread id the meeting-api expects.

    Prefer, in order: X-MICROSOFT-SKYPETEAMSPROPERTIES.cid (already-decoded thread id, no
    URL parsing at all) → X-MICROSOFT-SKYPETEAMSMEETINGURL → DESCRIPTION's LAST match."""
    if not ics or "BEGIN:VEVENT" not in ics:
        return {}
    ve = unfold_ics(ics).split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0]
    urls: dict[str, list[str]] = {}
    thread_id = None
    provider = None
    for line in ve.splitlines():
        name = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
        value = line.split(":", 1)[1] if ":" in line else ""
        unescaped = value.replace("\\,", ",").replace("\\;", ";")
        if name == "X-MICROSOFT-SKYPETEAMSPROPERTIES":
            m = re.search(r'"cid"\s*:\s*"([^"]+)"', unescaped)
            if m:
                thread_id = m.group(1)
        elif name == "X-MICROSOFT-ONLINEMEETINGINFORMATION":
            m = re.search(r'"OnlineMeetingProvider"\s*:\s*(\d+)', unescaped)
            if m:
                provider = int(m.group(1))
        if name in TEAMS_URL_PROPERTIES:
            found = [u.rstrip(">).,;") for u in _TEAMS_URL.findall(unescaped)]
            if found:
                urls[name] = found
    if not thread_id:
        for prop in ("X-MICROSOFT-SKYPETEAMSMEETINGURL", "DESCRIPTION"):
            for url in reversed(urls.get(prop, [])):
                parsed = teams_native_id(url)
                if parsed and parsed[1].startswith("19:meeting_"):
                    thread_id = parsed[1]
                    break
            if thread_id:
                break
    return {"thread_id": thread_id,
            "online_meeting_provider": provider,  # 3 == teamsForBusiness
            "join_urls": urls}


def teams_native_id(url: str) -> Optional[tuple[str, str]]:
    """(platform, native_meeting_id) for a Teams join URL — the pair POST /bots wants.

    A local mirror of meeting-api's canonical parser
    (`core/meetings/services/meeting-api/src/meeting_api/collector/meeting_link.py`
    :func:`parse_meeting_url`); the witness tree must stay import-free of the services,
    so this covers only the Teams branch. If the two ever disagree, that file wins."""
    value = urllib.parse.unquote(url or "")
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    if "teams.microsoft.com" not in host and "teams.live.com" not in host:
        return None
    thread = _TEAMS_THREAD.search(value)
    if thread:
        return ("teams", thread.group(0))
    short = _TEAMS_SHORT.search(urllib.parse.urlparse(url).path)
    if short:
        return ("teams", short.group(1))
    return None


def decode_header_text(value: str) -> str:
    """RFC 2047 header decoding.

    Exchange writes the subject as `=?Windows-1252?Q?Vexa_rig_=97_ICS_probe?=` whenever it
    contains a non-ASCII character — and an em dash is enough. `mail_real.poll` hands the
    header through raw, so a naive substring match on the subject silently never fires.
    Cost the rig one 4-minute polling window that reported FAIL while the mail sat in the
    inbox the whole time."""
    from email.header import decode_header
    out = []
    for chunk, enc in decode_header(value or ""):
        out.append(chunk.decode(enc or "utf-8", errors="replace")
                   if isinstance(chunk, bytes) else chunk)
    return "".join(out)


def iso_utc(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize_ics(ics: str) -> dict[str, Any]:
    """The structural read of an ICS — shape only, never the private body text."""
    unfolded = unfold_ics(ics)
    ve = unfolded.split("BEGIN:VEVENT", 1)[-1].split("END:VEVENT", 1)[0] if "BEGIN:VEVENT" in unfolded else ""
    props = sorted({ln.split(":", 1)[0].split(";", 1)[0].strip().upper()
                    for ln in ve.splitlines() if ln.strip() and ":" in ln})
    method = re.search(r"^METHOD:(.+)$", unfolded, re.M)
    uid = re.search(r"^UID:(.+)$", ve, re.M)
    dtstart = re.search(r"^DTSTART[^:]*:(.+)$", ve, re.M)
    return {"method": method.group(1).strip() if method else None,
            "uid": uid.group(1).strip() if uid else None,
            "dtstart": dtstart.group(1).strip() if dtstart else None,
            "properties": props,
            "teams_url_in": ics_teams_properties(ics),
            "teams": ics_teams_evidence(ics)}


# ── credentials + Graph client ────────────────────────────────────────────────────────

def load_creds() -> dict[str, str]:
    """Decrypt the vault into process env (idempotent). Values are NEVER printed."""
    if not all(os.environ.get(k) for k in REQUIRED):
        out = subprocess.run(["sops", "-d", VAULT], check=True,
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"vault is missing {', '.join(missing)} — see witness/README-m365.md")
    return {k: os.environ[k] for k in REQUIRED}


@dataclass
class Graph:
    creds: dict[str, str] = field(default_factory=load_creds)
    _tok: Optional[str] = None
    _exp: float = 0.0

    @property
    def organizer_id(self) -> str:
        return self.creds["VEXA_GRAPH_ORGANIZER_ID"]

    @property
    def organizer(self) -> str:
        return self.creds["VEXA_GRAPH_ORGANIZER"]

    def token(self) -> str:
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        body = urllib.parse.urlencode({
            "client_id": self.creds["VEXA_GRAPH_CLIENT_ID"],
            "client_secret": self.creds["VEXA_GRAPH_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"}).encode()
        url = f"https://login.microsoftonline.com/{self.creds['VEXA_GRAPH_TENANT_ID']}/oauth2/v2.0/token"
        with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as r:
            payload = json.load(r)
        self._tok = payload["access_token"]
        self._exp = time.time() + int(payload.get("expires_in", 3600))
        return self._tok

    def call(self, method: str, path: str, body: Any = None,
             params: Optional[dict[str, str]] = None, raw: bool = False) -> tuple[int, Any]:
        """One Graph call → (status, parsed-or-text). Never raises on an HTTP error:
        a failed Graph call is a RESULT the rig reports, not a crash (stop condition —
        record the exact error and continue)."""
        url = GRAPH + path
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token(),
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode(errors="replace")
                if raw:  # /$value returns MIME, not JSON
                    return r.status, text
                return r.status, (json.loads(text) if text.strip() else None)
        except urllib.error.HTTPError as e:  # noqa: PERF203
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw

    def must(self, method: str, path: str, body: Any = None,
             params: Optional[dict[str, str]] = None) -> Any:
        code, payload = self.call(method, path, body, params)
        if code >= 400:
            err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
            raise GraphError(f"{method} {path} → {code} "
                             f"{err.get('code', '')}: {err.get('message', payload)}")
        return payload


class GraphError(RuntimeError):
    pass


# ── ledger (so cleanup finds what a dead process created) ─────────────────────────────

def ledger_append(kind: str, ident: str, note: str = "") -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps({"kind": kind, "id": ident, "note": note,
                             "created": iso_utc(datetime.now(timezone.utc))}) + "\n")


def ledger_read() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def ledger_write(rows: list[dict]) -> None:
    LEDGER.write_text("".join(json.dumps(r) + "\n" for r in rows))


# ── verbs ─────────────────────────────────────────────────────────────────────────────

def verb_meeting_create(g: Graph, args) -> int:
    start = datetime.now(timezone.utc) + timedelta(minutes=args.start_in)
    body: dict[str, Any] = {
        "startDateTime": iso_utc(start),
        "endDateTime": iso_utc(start + timedelta(minutes=args.minutes)),
        "subject": args.subject,
    }
    if args.lobby_bypass:
        # Without this a bot sits in the lobby until a human admits it — which is
        # precisely the hands-free property the rig exists to provide.
        body["lobbyBypassSettings"] = {"scope": "everyone", "isDialInBypassEnabled": True}
    # NOTE: the path takes the organizer's objectId GUID. Passing the UPN yields
    # "Resource not found for the segment" / "not a valid GUID" — see README-m365.md.
    m = g.must("POST", f"/users/{g.organizer_id}/onlineMeetings", body)
    join = m.get("joinWebUrl", "")
    ledger_append("onlineMeeting", m["id"], args.subject)
    native = teams_native_id(join)
    say(f"meeting created · subject {args.subject!r} · starts {body['startDateTime']}")
    print(json.dumps({
        "onlineMeetingId": m["id"],
        "joinWebUrl": join,
        "platform": native[0] if native else None,
        "native_meeting_id": native[1] if native else None,
        "lobbyBypass": (m.get("lobbyBypassSettings") or {}).get("scope"),
        "allowedPresenters": m.get("allowedPresenters"),
    }, indent=2))
    if not native:
        say("WARNING: joinWebUrl did not parse to a (platform, native_id) pair")
        return 1
    return 0


def verb_invite_send(g: Graph, args) -> int:
    start = datetime.now(timezone.utc) + timedelta(minutes=args.start_in)
    body = {
        "subject": args.subject,
        "start": {"dateTime": iso_utc(start).rstrip("Z"), "timeZone": "UTC"},
        "end": {"dateTime": iso_utc(start + timedelta(minutes=args.minutes)).rstrip("Z"),
                "timeZone": "UTC"},
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "body": {"contentType": "text", "content": args.note},
        "attendees": [{"emailAddress": {"address": a}, "type": "required"}
                      for a in args.to],
    }
    ev = g.must("POST", f"/users/{g.organizer_id}/events", body)
    ledger_append("event", ev["id"], f"{args.subject} → {','.join(args.to)}")
    join = (ev.get("onlineMeeting") or {}).get("joinUrl") or ""
    native = teams_native_id(join)
    say(f"event created · Exchange is delivering the invitation to {', '.join(args.to)}")
    print(json.dumps({
        "eventId": ev["id"],
        "iCalUId": ev.get("iCalUId"),
        "webLink_present": bool(ev.get("webLink")),
        "isOnlineMeeting": ev.get("isOnlineMeeting"),
        "onlineMeetingProvider": ev.get("onlineMeetingProvider"),
        "joinUrl": join,
        "platform": native[0] if native else None,
        "native_meeting_id": native[1] if native else None,
        "location_displayName": (ev.get("location") or {}).get("displayName"),
    }, indent=2))
    return 0


def _attachments_ics(g: Graph, message_id: str) -> list[str]:
    payload = g.must("GET", f"/users/{g.organizer_id}/messages/{message_id}/attachments")
    out = []
    for att in payload.get("value", []):
        name = (att.get("name") or "").lower()
        ctype = (att.get("contentType") or "").lower()
        if "calendar" in ctype or name.endswith(".ics") or ctype == "application/ics":
            raw = att.get("contentBytes")
            if raw:
                import base64
                out.append(base64.b64decode(raw).decode(errors="replace"))
    return out


def verb_mail_poll(g: Graph, args) -> int:
    params = {"$top": str(args.top),
              "$select": "id,subject,from,receivedDateTime,hasAttachments",
              "$orderby": "receivedDateTime desc"}
    if args.since:
        params["$filter"] = f"receivedDateTime ge {args.since}"
        # $filter + $orderby on the same non-key property needs the two to agree; Graph
        # rejects the mix otherwise, so order by the filtered property only.
    payload = g.must("GET", f"/users/{g.organizer_id}/mailFolders/{args.folder}/messages",
                     params=params)
    rows = payload.get("value", [])
    say(f"{len(rows)} message(s) in {args.folder}"
        + (f" since {args.since}" if args.since else ""))
    hits = 0
    for m in rows:
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "?")
        ics_parts = _attachments_ics(g, m["id"]) if m.get("hasAttachments") else []
        line = (f"{m['receivedDateTime']} · {frm} · {m['subject'][:64]!r} · "
                f"ics={'yes' if ics_parts else 'no'}")
        print("  " + line)
        for ics in ics_parts:
            hits += 1
            summary = summarize_ics(ics)
            print("      " + json.dumps(summary))
            if args.print_ics:
                print("\n".join("      | " + l for l in unfold_ics(ics).splitlines()))
    if args.require_ics and not hits:
        say("FAIL: no ICS attachment found in the polled window")
        return 1
    return 0


def mime_calendar_parts(raw: str) -> list[str]:
    """Every text/calendar body inside a raw MIME message (pure — unit-tested offline)."""
    import email as _email
    msg = _email.message_from_string(raw)
    out: list[str] = []
    for part in msg.walk():
        ctype = (part.get_content_type() or "").lower()
        fname = (part.get_filename() or "").lower()
        if ctype in ("text/calendar", "application/ics") or fname.endswith(".ics"):
            payload = part.get_payload(decode=True)
            if payload:
                out.append(payload.decode(errors="replace"))
    return out


def verb_mail_sent_ics(g: Graph, args) -> int:
    """Read the SENT copy's raw MIME and report the ICS shape Microsoft composed.

    Honest labelling matters here: this proves what Exchange PUT ON THE WIRE, not that
    anything arrived. Use `gmail poll` for delivery."""
    params = {"$top": str(args.top),
              "$select": "id,subject,toRecipients,sentDateTime",
              "$orderby": "sentDateTime desc"}
    payload = g.must("GET", f"/users/{g.organizer_id}/mailFolders/sentitems/messages",
                     params=params)
    rows = payload.get("value", [])
    say(f"{len(rows)} message(s) in sentitems")
    hits = 0
    for m in rows:
        if args.subject_contains and args.subject_contains.lower() not in (m.get("subject") or "").lower():
            continue
        code, raw = g.call("GET", f"/users/{g.organizer_id}/messages/{m['id']}/$value", raw=True)
        if code >= 400 or not isinstance(raw, str):
            say(f"could not read MIME for {m['id'][:16]}… → {code}")
            continue
        to = ",".join((r.get("emailAddress") or {}).get("address", "?")
                      for r in m.get("toRecipients", []))
        parts = mime_calendar_parts(raw)
        print(f"  {m.get('sentDateTime')} · to {to} · {m.get('subject','')[:64]!r} · "
              f"ics={'yes' if parts else 'no'} · mime_bytes={len(raw)}")
        for ics in parts:
            hits += 1
            print("      " + json.dumps(summarize_ics(ics)))
            if args.print_ics:
                print("\n".join("      | " + l for l in unfold_ics(ics).splitlines()))
    if args.require_ics and not hits:
        say("FAIL: no text/calendar part in any sent message in the window")
        return 1
    return 0


def verb_gmail_poll(g: Graph, args) -> int:
    """THE delivery proof — poll the external mailbox we control (vexa-mail vault).

    Exchange suppresses self-delivery, so the only truthful way to watch an invitation
    ARRIVE is to send it somewhere else. This verb needs no Graph credential at all; it
    is in this rig because it closes the loop `invite send --to <that address>` opens."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mail_real as mr  # noqa: E402

    deadline = time.time() + args.wait
    seen: set[int] = set()
    hits = 0
    while True:
        for msg in mr.poll(since_uid=args.since_uid):
            if msg.uid in seen:
                continue
            seen.add(msg.uid)
            subject = decode_header_text(msg.subject)
            if args.subject_contains and args.subject_contains.lower() not in subject.lower():
                continue
            print(f"  uid={msg.uid} · from {msg.from_addr} · {subject[:64]!r} · "
                  f"ics={'yes' if msg.ics else 'no'}")
            if msg.ics:
                hits += 1
                print("      " + json.dumps(summarize_ics(msg.ics)))
                if args.print_ics:
                    print("\n".join("      | " + l for l in unfold_ics(msg.ics).splitlines()))
        if hits or time.time() >= deadline:
            break
        say(f"nothing yet — retrying in {args.interval}s")
        time.sleep(args.interval)
    if args.require_ics and not hits:
        say(f"FAIL: no invitation with an ICS arrived within {args.wait}s")
        return 1
    return 0


def verb_cleanup(g: Graph, args) -> int:
    rows = ledger_read()
    if not rows:
        say("ledger is empty — nothing the rig created is outstanding")
        return 0
    kept: list[dict] = []
    for row in rows:
        kind, ident = row["kind"], row["id"]
        if args.dry_run:
            print(f"  would delete {kind} {ident[:24]}… ({row.get('note','')})")
            kept.append(row)
            continue
        if kind == "event":
            # cancel (not delete) when attendees exist: Exchange must send the
            # cancellation so no invitee is left holding a phantom meeting.
            code, payload = g.call("POST", f"/users/{g.organizer_id}/events/{ident}/cancel",
                                   {"comment": "Vexa test rig cleanup."})
            if code >= 400:
                code, payload = g.call("DELETE", f"/users/{g.organizer_id}/events/{ident}")
        else:
            code, payload = g.call("DELETE", f"/users/{g.organizer_id}/onlineMeetings/{ident}")
        if code < 400 or code == 404:
            say(f"removed {kind} {ident[:24]}… ({code})")
        else:
            err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
            say(f"FAILED to remove {kind} {ident[:24]}… → {code} {err.get('code','')}: "
                f"{err.get('message', '')}")
            kept.append(row)
    ledger_write(kept)
    remaining = len(kept)
    say(f"tenant clean · {remaining} item(s) still outstanding" if remaining
        else "tenant clean · ledger empty")
    return 0 if remaining == 0 or args.dry_run else 1


def verb_whoami(g: Graph, args) -> int:
    """What the credential can actually do — the first thing to run in a new tenant."""
    import base64
    claims = json.loads(base64.urlsafe_b64decode(g.token().split(".")[1] + "=="))
    print(json.dumps({"app": claims.get("app_displayname"),
                      "roles": sorted(claims.get("roles", [])),
                      "tenant_matches_vault": claims.get("tid") == g.creds["VEXA_GRAPH_TENANT_ID"],
                      "organizer": g.organizer}, indent=2))
    code, _ = g.call("GET", f"/users/{g.organizer_id}/mailFolders/inbox",
                     params={"$select": "displayName"})
    print(json.dumps({"mailbox_readable": code == 200, "mailbox_status": code}, indent=2))
    return 0 if code == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365_rig", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="verb", required=True)

    who = sub.add_parser("whoami", help="what the app credential can do in this tenant")
    who.set_defaults(fn=verb_whoami)

    mt = sub.add_parser("meeting", help="Teams online meetings")
    mtsub = mt.add_subparsers(dest="sub", required=True)
    mc = mtsub.add_parser("create", help="create a real Teams meeting")
    mc.add_argument("--start-in", type=int, default=5, metavar="MINUTES")
    mc.add_argument("--minutes", type=int, default=30, help="duration")
    mc.add_argument("--lobby-bypass", action="store_true",
                    help="everyone bypasses the lobby (hands-free bot admission)")
    mc.add_argument("--subject", default="Vexa rig — Teams meeting")
    mc.set_defaults(fn=verb_meeting_create)

    inv = sub.add_parser("invite", help="calendar invitations")
    invsub = inv.add_subparsers(dest="sub", required=True)
    isend = invsub.add_parser("send", help="create an online-meeting EVENT with attendees "
                                           "so Exchange delivers a real ICS invitation")
    isend.add_argument("--to", action="append", required=True, metavar="ADDR",
                       help="attendee address (repeatable)")
    isend.add_argument("--start-in", type=int, default=10, metavar="MINUTES")
    isend.add_argument("--minutes", type=int, default=30)
    isend.add_argument("--subject", default="Vexa rig — invite intake")
    isend.add_argument("--note", default="Generated by the Vexa M365 test rig.")
    isend.set_defaults(fn=verb_invite_send)

    ml = sub.add_parser("mail", help="the tenant mailbox over Graph")
    mlsub = ml.add_subparsers(dest="sub", required=True)
    mp = mlsub.add_parser("poll", help="what arrived, and which ICS properties carry the link")
    mp.add_argument("--since", metavar="ISO8601", help="e.g. 2026-08-24T15:00:00Z")
    mp.add_argument("--top", type=int, default=10)
    mp.add_argument("--folder", default="inbox")
    mp.add_argument("--print-ics", action="store_true", help="dump the unfolded ICS")
    mp.add_argument("--require-ics", action="store_true",
                    help="exit non-zero when the window holds no ICS (assertion mode)")
    mp.set_defaults(fn=verb_mail_poll)

    ms = mlsub.add_parser("sent-ics", help="the ICS bytes Exchange composed, read from the "
                                           "SENT copy's raw MIME (not a delivery proof)")
    ms.add_argument("--top", type=int, default=10)
    ms.add_argument("--subject-contains", metavar="TEXT")
    ms.add_argument("--print-ics", action="store_true")
    ms.add_argument("--require-ics", action="store_true")
    ms.set_defaults(fn=verb_mail_sent_ics)

    gm = sub.add_parser("gmail", help="the EXTERNAL mailbox we control (delivery proof)")
    gmsub = gm.add_subparsers(dest="sub", required=True)
    gp = gmsub.add_parser("poll", help="wait for the invitation Exchange delivered and "
                                       "report which ICS properties carry the Teams link")
    gp.add_argument("--since-uid", type=int, default=0)
    gp.add_argument("--subject-contains", metavar="TEXT")
    gp.add_argument("--wait", type=int, default=180, help="seconds to keep polling")
    gp.add_argument("--interval", type=int, default=15)
    gp.add_argument("--print-ics", action="store_true")
    gp.add_argument("--require-ics", action="store_true")
    gp.set_defaults(fn=verb_gmail_poll)

    cl = sub.add_parser("cleanup", help="remove everything the rig created")
    cl.add_argument("--dry-run", action="store_true")
    cl.set_defaults(fn=verb_cleanup)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(Graph(), args)
    except GraphError as e:
        say(f"GRAPH ERROR · {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
