"""The doors — one method per catalogue verb, plus the reads the verify block and the reset need.

THE RULE THIS FILE EXISTS TO KEEP: a state is entered through the product's own doors. Not a
`docker exec … psql`, not a write into the workspace volume, not a redis SET. Every write below is
an HTTP call to a service route or an SMTP message into the mail double — the same calls a person,
a calendar or a mail client makes. That is what makes a state re-enterable on a stack that is
running someone else's work, and it is why `states.yaml` names the door on every row.

THREE DELIBERATE EXCEPTIONS, all of them READS or per-subject deletes, all of them named here
rather than discovered later:

  1. `live_meetings()` reads the meetings table through the postgres container. There is no
     instance-wide live-meeting route (`GET /bots/status` is per-caller), and the refusal it feeds
     — never touch the stack while somebody's meeting is running — is the one guard that protects
     the founder's data. Fail CLOSED: if the probe cannot run, `rehearse()` refuses. Filed as a
     missing route rather than smuggled in as a habit.
  2. `session_keys_delete()` / `scaffold_keys_delete()` in `subject_reset` clear redis by the
     exact per-subject prefixes `blank-instance.sh` documents, and nothing else. agent-api owns
     those keys and exposes no delete for them.
  3. `lane_rows_delete_for()` deletes that subject's rows from the flows lanes — the reactions the
     engine admitted for them, their receipts and signals, their mail threads, and their friction.
     Same shape as the other two: the rows are per-subject and no route removes them. It is the
     one that makes a state RE-ENTERABLE — `admit()` dedups on (source_event_id, flow), so a
     reaction from an earlier run silently swallows the next invite (`admitted 0`, no prepare
     mail, a step that waits out its budget for a touch nothing will send).

`Doors` is a plain class with no HTTP in it, so `stub_doors.StubDoors` subclasses it and
`run_all.py --stub` proves every recipe offline. `LiveDoors` is the only thing that talks to the
stack.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import smtplib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

# ── deployment values, every one of them named ───────────────────────────────────────────────────
ADMIN_API = os.environ.get("VEXA_ADMIN_API_URL", "http://localhost:18457")
AGENT_API = os.environ.get("VEXA_AGENT_API_URL", "http://localhost:18500")
GATEWAY = os.environ.get("VEXA_GATEWAY_URL", "http://localhost:18456")
FLOWS_API = os.environ.get("VEXA_FLOWS_API_URL", "http://localhost:18200")
MAILPIT = os.environ.get("VEXA_MAILPIT_URL", os.environ.get("MAILPIT_URL", "http://localhost:8025"))
SMTP_ADDR = os.environ.get("VEXA_SMTP", "127.0.0.1:1025")
TERMINAL = os.environ.get("VEXA_UI_URL", "http://localhost:18300").rstrip("/")
MAIL_ADDR = os.environ.get("VEXA_MAIL_ADDR", "vexa@storm.test")
STACK = os.environ.get("VEXA_DOGFOOD_STACK", "vexa-dogfood")

# ── which harness a rehearsed subject runs on (PRD decisions 37 + 38) ────────────────────────────
#
# A runner is a per-SUBJECT dial, never a deployment one: `rehearse(..., runner="openai-agent")`
# pins one scratch subject to our own agent loop over the CCC box's Qwen while every other person
# on the instance — the founder included — keeps the deployment's default. The mechanism is the
# per-subject model config admin-api already resolves (`/internal/users/{id}/model-config`), which
# `dispatch.overlay_model_config` stamps into the worker's env; `runner` is one more field of it.
#
# THE TABLE IS THE VOCABULARY. `runner=` accepts exactly these names, and a typo is refused with
# the list rather than passed through — a runner nothing recognises is dropped silently at
# dispatch, which is a preference that reads as set and does nothing. Every value is overridable
# from the environment so the endpoint is a deployment fact, not a constant in this file.
RUNNER_DIALS: dict[str, dict] = {
    # The deployment's own harness: no custom endpoint, no model pin — clearing the fields returns
    # the subject to whatever the instance runs.
    "claude-code": {"mode": "", "base_url": "", "model": "", "extra_body": ""},
    # PRD decision 37's first test target. `extra_body` is load-bearing, not decoration: vLLM/Qwen
    # returns no valid JSON at all unless thinking is disabled.
    "openai-agent": {
        "mode": "custom",
        "base_url": os.environ.get("VEXA_REHEARSE_LLM_BASE_URL", "http://192.168.1.6:8001/v1"),
        "model": os.environ.get("VEXA_REHEARSE_LLM_MODEL", "qwen3.8-27b"),
        "extra_body": os.environ.get(
            "VEXA_REHEARSE_LLM_EXTRA_BODY",
            '{"chat_template_kwargs":{"enable_thinking":false}}'),
    },
}


def runner_config(runner: str) -> dict:
    """The model config that pins a subject to `runner`, or raise with the names that work."""
    dials = RUNNER_DIALS.get(runner)
    if dials is None:
        raise DoorRefused(
            f"{runner!r} is not a runner this tool knows. Available: "
            f"{', '.join(sorted(RUNNER_DIALS))}. A name passed through unchecked would be dropped "
            f"at dispatch and the subject would quietly run on the deployment's default — a "
            f"preference that reads as set and does nothing.")
    return {"runner": runner, **dials}



class DoorRefused(RuntimeError):
    """A door answered no, and the answer is the product. Never swallowed, never retried blind."""


class Doors:
    """The interface. Every method is a verb in `catalogue.VERBS` or a read a check needs.

    The base class raises: an unimplemented door must be loud. A stub that silently returns None
    would let `run_all.py` report a state green having proved nothing, which is the exact failure
    the verify block exists to prevent.
    """

    # ── verbs ────────────────────────────────────────────────────────────────────────────────
    def require_instance_blank(self) -> dict: raise NotImplementedError
    def require_subject_absent(self, address: str) -> dict: raise NotImplementedError
    def user_ensure(self, address: str) -> dict: raise NotImplementedError
    def desk_init(self, subject: str) -> dict: raise NotImplementedError
    def desk_entity(self, subject: str, kind: str, name: str, facts=(), source: str = "",
                    summary: str = "", slug: str = "") -> dict: raise NotImplementedError
    def group_new(self, owner: str, name: str, purpose: str = "") -> dict: raise NotImplementedError
    def group_join(self, group: str, owner: str, member: str, member_email: str = "",
                   role: str = "contributor") -> dict: raise NotImplementedError
    def request_sign_in_link(self, address: str) -> dict: raise NotImplementedError
    def drop_invite(self, organizer: str, title: str, start: float, attendees=(),
                    ics_uid: str = "", group: str = "", url: str = "") -> dict:
        raise NotImplementedError
    def seed_meeting(self, owner: str, native: str, title: str, segments: list,
                     started_at: float, source: str = "seed") -> dict: raise NotImplementedError
    def emit_fact(self, event_type: str, source_event_id: str, refs: dict) -> dict:
        raise NotImplementedError
    def await_mail(self, to: str, subject_contains: str = "", budget_s: int = 180,
                   since: float = 0.0) -> dict: raise NotImplementedError
    def reply_to_mail(self, message: dict, from_address: str, body: str) -> dict:
        raise NotImplementedError
    def await_reaction(self, flow: str, since: float = 0.0, budget_s: int = 300) -> dict:
        raise NotImplementedError
    def cancel_bot_leg(self, flow: str, source_contains: str = "") -> dict:
        raise NotImplementedError

    # ── the per-subject harness (decisions 37 + 38) ──────────────────────────────────────────
    def bind_runner(self, subject: str, runner: str) -> dict: raise NotImplementedError

    # ── reads the verify block needs ─────────────────────────────────────────────────────────
    def user_find(self, address: str): raise NotImplementedError
    def user_email(self, uid: str) -> str: raise NotImplementedError
    def meeting_get(self, owner: str, meeting_id) -> dict: raise NotImplementedError
    def desk_tree(self, subject: str, slug: str = "") -> list: raise NotImplementedError
    def group_members(self, owner: str, group: str) -> list: raise NotImplementedError
    def scaffold_get(self, scaffold_id: str, subject: str = "") -> dict: raise NotImplementedError

    # ── the guard, and the reset ─────────────────────────────────────────────────────────────
    def live_meetings(self) -> list: raise NotImplementedError
    def user_delete(self, uid: str) -> dict: raise NotImplementedError
    def desk_delete(self, subject: str) -> dict: raise NotImplementedError
    def meetings_delete_for(self, subject: str) -> int: raise NotImplementedError
    def session_keys_delete(self, subject: str) -> int: raise NotImplementedError
    def scaffold_keys_delete(self, address: str) -> int: raise NotImplementedError
    def friction_delete_for(self, subject: str) -> int: raise NotImplementedError
    def lane_rows_delete_for(self, subject: str, address: str) -> dict:
        raise NotImplementedError
    def mail_delete_for(self, address: str) -> int: raise NotImplementedError


# ── the live implementation ──────────────────────────────────────────────────────────────────────

#: `GET /meetings` caps `limit` at 100 and answers 422 above it. Named here because asking for
#: more than a route allows is not a bigger answer, it is no answer — and the shape of the refusal
#: (a dict with `detail`) reads as an empty list to anything that does `.get("meetings", [])`.
MEETINGS_PAGE_MAX = 100


def _http(method: str, url: str, headers: dict | None = None, body=None, timeout: float = 40):
    h = {"content-type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:                                        # noqa: BLE001 — transport
        return 0, f"{type(e).__name__}: {e}"


class LiveDoors(Doors):
    """The doors of the running dogfood stack."""

    def __init__(self, *, admin_key: str = "", flows_key: str = "", stack: str = STACK):
        self.stack = stack
        self._admin_key = admin_key or _admin_key(stack)
        self._flows_key = flows_key or _flows_key()
        self._keys: dict[str, str] = {}

    # -- identity -------------------------------------------------------------------------------
    def _ak(self) -> dict:
        return {"X-Admin-API-Key": self._admin_key}

    def _user_key(self, uid: str) -> str:
        """One gateway key per subject per process. Minted once and remembered — the rig learned
        this the expensive way (66 live credentials for one account, none of them revocable)."""
        uid = str(uid)
        if uid not in self._keys:
            st, tok = _http("POST", f"{ADMIN_API}/admin/users/{uid}/tokens", self._ak(),
                            {"scopes": ["bot", "browser", "tx"]})
            key = (tok or {}).get("token") if isinstance(tok, dict) else ""
            if not key:
                raise DoorRefused(f"admin-api would not mint a key for uid {uid} ({st})")
            self._keys[uid] = key
        return self._keys[uid]

    def _gw(self, uid: str, method: str, path: str, body=None, timeout: float = 60):
        return _http(method, f"{GATEWAY}{path}", {"X-API-Key": self._user_key(uid)}, body, timeout)

    # -- verbs ----------------------------------------------------------------------------------
    def require_instance_blank(self) -> dict:
        st, body = _http("GET", f"{AGENT_API}/api/global/state", None)
        if st != 200 or not isinstance(body, dict):
            st, body = _http("GET", f"{ADMIN_API}/internal/instance-state", self._ak())
        if st != 200 or not isinstance(body, dict):
            raise DoorRefused(
                "could not read the instance gate — refusing rather than guessing that a stack "
                f"is blank ({st}). The gate is what tells a blank instance from a claimed one.")
        claimed = bool(body.get("admin_exists") or body.get("admin"))
        layer = str(body.get("global_setup") or body.get("setup") or "")
        if claimed or layer == "completed":
            raise DoorRefused(
                "the instance is NOT blank: an admin has claimed it"
                + (f" and the company layer is {layer}" if layer else "")
                + ". `blank-admin` asserts this state, it never creates it — blanking deletes "
                  "every person on the stack and is `bin/blank-instance.sh`, run on purpose.")
        return {"blank": True, "admin_exists": claimed, "global_setup": layer}

    def require_subject_absent(self, address: str) -> dict:
        uid = self.user_find(address)
        if uid:
            raise DoorRefused(
                f"{address} already has a user (uid {uid}) and this state needs a stranger. "
                f"Run subject_reset({address!r}) first.")
        return {"absent": True, "address": address}

    def user_ensure(self, address: str) -> dict:
        st, u = _http("GET", f"{ADMIN_API}/admin/users/email/{urllib.parse.quote(address)}",
                      self._ak())
        existed = st == 200
        if not existed:
            st, u = _http("POST", f"{ADMIN_API}/admin/users", self._ak(),
                          {"email": address, "name": address.split("@")[0].replace(".", " ").title()})
        if not isinstance(u, dict) or not u.get("id"):
            raise DoorRefused(f"admin-api would not resolve or create {address}: {st} {str(u)[:200]}")
        return {"uid": str(u["id"]), "email": address, "existed": existed}

    def desk_init(self, subject: str) -> dict:
        st, body = _http("POST", f"{AGENT_API}/api/workspace/init", {"X-User-Id": str(subject)}, {})
        if st not in (200, 201):
            raise DoorRefused(f"workspace/init refused for {subject}: {st} {str(body)[:200]}")
        return {"subject": str(subject), "status": st}

    def desk_entity(self, subject: str, kind: str, name: str, facts=(), source: str = "",
                    summary: str = "", slug: str = "") -> dict:
        payload = {"kind": kind, "name": name, "facts": list(facts), "source": source}
        if summary:
            payload["summary"] = summary
        if slug:
            payload["slug"] = slug
        st, body = _http("POST", f"{AGENT_API}/api/workspace/entity",
                         {"X-User-Id": str(subject)}, payload)
        if st not in (200, 201):
            raise DoorRefused(f"entity write refused for {subject}: {st} {str(body)[:300]}")
        return body if isinstance(body, dict) else {"status": st}

    def group_new(self, owner: str, name: str, purpose: str = "") -> dict:
        st, r = _http("POST", f"{AGENT_API}/api/workspace/shared/new",
                      {"X-User-Id": str(owner)}, {"name": name})
        if st not in (200, 201) or not isinstance(r, dict) or not r.get("workspace_id"):
            raise DoorRefused(f"could not create the group {name!r}: {st} {str(r)[:200]}")
        wid = str(r["workspace_id"])
        if purpose:
            _http("POST", f"{AGENT_API}/api/workspace/purpose", {"X-User-Id": str(owner)},
                  {"slug": wid, "purpose": purpose})
        return {"workspace_id": wid, "name": name, "owner": str(owner)}

    def group_join(self, group: str, owner: str, member: str, member_email: str = "",
                   role: str = "contributor") -> dict:
        already = [m for m in self.group_members(owner, group)
                   if str(m.get("subject")) == str(member) or m.get("email") == member_email]
        if already:
            return {"group": group, "member": str(member), "joined": False, "already": True}
        body = {"workspace_id": group, "role": role, "expires_in_sec": 3600, "max_uses": 1,
                "mode": "restricted" if member_email else "open"}
        if member_email:
            body["allowed_emails"] = [member_email]
        st, r = _http("POST", f"{AGENT_API}/api/workspace/invites", {"X-User-Id": str(owner)}, body)
        if st not in (200, 201) or not isinstance(r, dict) or not r.get("token"):
            raise DoorRefused(f"could not mint an invite to {group}: {st} {str(r)[:200]}")
        hdr = {"X-User-Id": str(member)}
        if member_email:
            hdr["X-User-Email"] = member_email
        st2, r2 = _http("POST", f"{AGENT_API}/api/workspace/invites/accept", hdr,
                        {"token": r["token"]})
        if st2 not in (200, 201):
            raise DoorRefused(f"{member} could not redeem the invite to {group}: "
                              f"{st2} {str(r2)[:200]}")
        return {"group": group, "member": str(member), "joined": True}

    def request_sign_in_link(self, address: str) -> dict:
        st, body = _http("POST", f"{TERMINAL}/api/auth/request-link", None, {"email": address})
        # The route answers 200 whether or not the address is known — deliberately, so it cannot be
        # used to enumerate accounts. So "sent" is not proved here; the mail is.
        if st not in (200, 202):
            raise DoorRefused(f"the sign-in door refused {address}: {st} {str(body)[:200]}. "
                              f"VEXA_UI_URL is {TERMINAL} — is that this deployment's terminal?")
        return {"requested": address, "status": st, "terminal": TERMINAL}

    def drop_invite(self, organizer: str, title: str, start: float, attendees=(),
                    ics_uid: str = "", group: str = "", url: str = "") -> dict:
        """SMTP an ICS invite to the mailbox the poller answers as — the calendar's own door.

        Deliberately identical in shape to `bin/drop-invite.py`, which is the hand tool; this is
        the same message built in-process so a recipe does not shell out.
        """
        start = float(start)
        fmt = lambda t: time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))          # noqa: E731
        uid = ics_uid or f"rehearse-{int(start)}@vexa.local"
        url = url or "https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH"
        lines = [
            "BEGIN:VCALENDAR", "PRODID:-//Vexa//rehearse//EN", "VERSION:2.0", "METHOD:REQUEST",
            "BEGIN:VEVENT", f"DTSTART:{fmt(start)}", f"DTEND:{fmt(start + 3600)}", f"UID:{uid}",
            f"DTSTAMP:{fmt(time.time())}",
            f"ORGANIZER;CN={organizer.split('@')[0]}:mailto:{organizer}",
        ]
        for name, addr in attendees:
            lines.append("ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;"
                         f"CN={name}:mailto:{addr}")
        lines.append(f"ATTENDEE;CN=Vexa;PARTSTAT=NEEDS-ACTION:mailto:{MAIL_ADDR}")
        desc = "Join Zoom Meeting" + (f" #group:{group}" if group else "") + f"\\n{url}"
        lines += [f"SUMMARY:{title}", f"DESCRIPTION:{desc}", f"LOCATION:{url}",
                  "END:VEVENT", "END:VCALENDAR", ""]
        m = EmailMessage()
        m["From"] = f"{organizer.split('@')[0]} <{organizer}>"
        m["To"] = MAIL_ADDR
        m["Subject"] = (f"Invitation: {title} @ "
                        f"{time.strftime('%a %b %d, %Y %H:%M', time.gmtime(start))} (UTC)")
        m["Date"] = formatdate(usegmt=True)
        m["Message-ID"] = make_msgid(domain="rehearse.local")
        m.set_content(f"You have been invited to {title}.\n{url}\n")
        m.add_attachment("\r\n".join(lines).encode(), maintype="text", subtype="calendar",
                         filename="invite.ics", params={"method": "REQUEST", "charset": "utf-8"})
        host, _, port = SMTP_ADDR.partition(":")
        with smtplib.SMTP(host, int(port or 1025), timeout=20) as s:
            s.send_message(m)
        return {"ics_uid": uid, "to": MAIL_ADDR, "organizer": organizer, "start": start,
                "attendees": [a for _, a in attendees], "message_id": m["Message-ID"]}

    def seed_meeting(self, owner: str, native: str, title: str, segments: list,
                     started_at: float, source: str = "seed") -> dict:
        """`POST /meetings` then `POST /meetings/{id}/transcript-import` — two gateway calls.

        A jitsi URL, so the native id survives verbatim (meeting-api's google_meet rule would force
        a synthetic code and break the row's addressability — and an unaddressable row is a mail
        with a button that opens nothing).
        """
        duration = max((float(s["end"]) for s in segments), default=0.0)
        started = float(started_at)
        ended = started + duration
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))       # noqa: E731
        # ADOPT BEFORE CREATING. The unique index on (user, platform, native) is PARTIAL — it does
        # not cover completed rows — so a second seed of the same native id would happily mint a
        # SECOND completed meeting. The recipe's fact id names the row, so that second row would
        # produce a second fan-out: a duplicate mail to everybody, from a call whose whole contract
        # is idempotence. Adopting is also the truthful answer: this transcript is already imported
        # into that row, and the import route is itself idempotent on (source, row).
        existing = self._find_meeting(owner, native)
        if existing and existing.get("status") == "completed":
            return self._row(existing, started, ended, imported=False)
        st, m = self._gw(owner, "POST", "/meetings", {
            "title": title, "scheduled_at": iso(started),
            "meeting_url": f"https://meet.jit.si/{native}"})
        if st == 409:
            row = self._find_meeting(owner, native)
            if row and row.get("status") == "completed":
                return self._row(row, started, ended, imported=False)
            raise DoorRefused(
                f"a non-terminal meeting already holds native id {native} — seed under a different "
                f"native id, or delete the leftover row first")
        if st not in (200, 201) or not isinstance(m, dict):
            raise DoorRefused(f"meeting create refused: {st} {str(m)[:300]}")
        if m.get("platform") in (None, "", "unknown") or not m.get("native_meeting_id"):
            raise DoorRefused(
                f"the seed made an UNADDRESSABLE row (id {m.get('id')}): no share can be minted "
                f"against it, so the attendee mail would ship with a button that opens nothing")
        mid = m["id"]
        st, body = self._gw(owner, "POST", f"/meetings/{mid}/transcript-import", {
            "segments": [{"start": float(s["start"]), "end": float(s["end"]),
                          "speaker": s.get("speaker"), "text": s.get("text") or "",
                          "language": s.get("language") or "en"} for s in segments],
            "started_at": iso(started), "ended_at": iso(ended),
            # `source` is a CLOSED vocabulary on the route — `import` | `seed` — and it refuses
            # anything else with the list. It sent "rehearse" and got a 422 on the first live run.
            # The route is RIGHT and the tool was wrong: these words did not come from a recording,
            # they were imported from a fixture by a double, which is exactly what `seed` means.
            # Widening the vocabulary to admit a caller's own name for itself would make the column
            # unreadable, which is the column's whole job.
            "source": source or "seed"},
            timeout=180)
        if st != 200 or not isinstance(body, dict):
            raise DoorRefused(f"transcript-import refused on meeting {mid}: {st} {str(body)[:300]}")
        return self._row({**m, **body, "id": mid}, started, ended, imported=True)

    @staticmethod
    def _row(m: dict, started: float, ended: float, imported: bool) -> dict:
        return {"meeting_id": m.get("id"), "native_meeting_id": m.get("native_meeting_id"),
                "platform": m.get("platform"), "status": m.get("status"),
                "segments_loaded": m.get("segments_imported"), "imported": imported,
                "started_epoch": int(started), "ended_epoch": int(ended),
                "start_time": m.get("start_time"), "end_time": m.get("end_time")}

    def _meetings_of(self, owner: str) -> list:
        """This subject's meetings — and a REFUSAL IS NOT AN EMPTY LIST.

        Both callers used to read the response as `(b or {}).get("meetings", [])`, which turns any
        non-2xx into "this person has no meetings". Found live: the list was requested with
        `?limit=200`, the route caps it at 100 and answered 422, and `subject_reset` reported
        `meetings: 0` with a row sitting right there — then the next run of that state was refused
        with a 409 nobody could explain. The other caller is worse: `seed_meeting` asks this to
        decide whether a completed row already exists, so a swallowed refusal would have minted a
        SECOND completed meeting and mailed the whole room twice.

        This is the defect the whole package is written against, in its own code: a call that fails
        and is read as "nothing to do".
        """
        st, b = self._gw(owner, "GET", f"/meetings?limit={MEETINGS_PAGE_MAX}")
        if st != 200 or not isinstance(b, dict) or "meetings" not in b:
            raise DoorRefused(
                f"could not list meetings for {owner}: {st} {str(b)[:200]}. Refusing to read that "
                f"as 'no meetings' — every caller here decides something destructive from it.")
        return list(b.get("meetings") or [])

    def _find_meeting(self, owner: str, native: str) -> dict | None:
        rows = self._meetings_of(owner)
        return next((r for r in rows if str(r.get("native_meeting_id")) == str(native)), None)

    def emit_fact(self, event_type: str, source_event_id: str, refs: dict) -> dict:
        """`POST /events` on flows-api — the intake for a producer that is not the mailbox.

        NOT the control MCP's `fact_emit`: that verb is gated to an instance ADMIN because it
        injects facts naming an arbitrary organizer, and a harness is not a person. flows-api's
        own intake with the lane key is the door built for exactly this caller.
        """
        st, body = _http("POST", f"{FLOWS_API}/events",
                         {"X-Flows-Admin-Key": self._flows_key, "X-Actor": "rehearse"},
                         {"event_type": event_type, "source_event_id": source_event_id,
                          "refs": refs})
        if st not in (200, 202) or not isinstance(body, dict):
            raise DoorRefused(f"the fact intake refused {event_type}: {st} {str(body)[:300]}")
        return {"event_type": event_type, "source_event_id": source_event_id,
                "reactions_created": body.get("reactions_created"),
                "duplicate": bool(body.get("duplicate")), "at": time.time()}

    def await_mail(self, to: str, subject_contains: str = "", budget_s: int = 180,
                   since: float = 0.0) -> dict:
        deadline = time.time() + budget_s
        query = urllib.parse.quote(f"to:{to}")
        last: list = []
        while True:
            st, body = _http("GET", f"{MAILPIT}/api/v1/search?query={query}&limit=60", None)
            msgs = (body or {}).get("messages", []) if isinstance(body, dict) else []
            last = msgs
            unplaceable = 0
            for msg in msgs:
                if subject_contains and subject_contains.lower() not in (msg.get("Subject") or "").lower():
                    continue
                when = _mail_epoch(msg)
                if since and when is not None and when < since - 5:
                    continue                        # a previous run's touch, definitely
                if since and when is None:
                    # We cannot place it in time. INCLUDE it and say so: a false accept is a check
                    # that needs tightening, a false reject is a touch reported as never sent.
                    unplaceable += 1
                return self._mail(msg["ID"])
            if time.time() >= deadline:
                # The refusal NAMES what it saw and why each candidate was rejected — the previous
                # version listed the very mail it had just discarded, which reads as a product
                # failure and was a reader's.
                seen = "; ".join(sorted({(m.get("Subject") or "") for m in last})[:6])
                raise DoorRefused(
                    f"no mail to {to}"
                    + (f" whose subject contains {subject_contains!r}" if subject_contains else "")
                    + f" arrived within {budget_s}s (only counting mail newer than "
                      f"{time.strftime('%H:%M:%SZ', time.gmtime(since))} — this run's start)"
                    + f". {len(last)} message(s) to that address exist: {seen}")
            time.sleep(3)

    def _mail(self, message_id: str) -> dict:
        st, body = _http("GET", f"{MAILPIT}/api/v1/message/{message_id}", None)
        if st != 200 or not isinstance(body, dict):
            raise DoorRefused(f"mailpit would not read message {message_id}: {st}")
        text = str(body.get("Text") or "")
        html = str(body.get("HTML") or "")
        return {"id": message_id, "subject": body.get("Subject") or "",
                "to": [t.get("Address") for t in body.get("To") or []],
                "from": (body.get("From") or {}).get("Address"),
                "message_id": body.get("MessageID") or "",
                "text": text, "html": html, "body": text + "\n" + html,
                "links": _links(text + "\n" + html), "at": time.time()}

    def reply_to_mail(self, message: dict, from_address: str, body: str) -> dict:
        orig = str(message.get("message_id") or "")
        if not orig:
            raise DoorRefused(
                "the mail we are replying to carries no Message-ID, so `mail_thread` cannot route "
                "the reply and the poller would treat it as a new sender. Not sending.")
        if not orig.startswith("<"):
            orig = f"<{orig}>"
        m = EmailMessage()
        m["From"] = from_address
        m["To"] = MAIL_ADDR
        m["Subject"] = "Re: " + str(message.get("subject") or "")
        m["Date"] = formatdate(usegmt=True)
        m["Message-ID"] = make_msgid(domain="rehearse.local")
        m["In-Reply-To"] = orig
        m["References"] = orig
        m.set_content(body.strip() + "\n")
        host, _, port = SMTP_ADDR.partition(":")
        with smtplib.SMTP(host, int(port or 1025), timeout=20) as s:
            s.send_message(m)
        return {"in_reply_to": orig, "message_id": m["Message-ID"], "from": from_address,
                "sent_at": time.time()}

    def await_reaction(self, flow: str, since: float = 0.0, budget_s: int = 300) -> dict:
        deadline = time.time() + budget_s
        while True:
            st, body = _http("GET", f"{FLOWS_API}/reactions?limit=80",
                             {"X-Flows-Admin-Key": self._flows_key})
            rows = (body or {}).get("reactions", []) if isinstance(body, dict) else []
            for r in rows:
                if r.get("flow") != flow:
                    continue
                if since and float(r.get("created_at") or r.get("admitted_at") or 0) < since - 5:
                    continue
                return {"flow": flow, "state": r.get("state"), "id": r.get("id"),
                        "reaction": r, "admitted": True}
            if time.time() >= deadline:
                raise DoorRefused(
                    f"no `{flow}` reaction appeared within {budget_s}s. The fact reached the "
                    f"intake but nothing reacted — check that the lane is running the flow "
                    f"version that declares it.")
            time.sleep(4)

    def bind_runner(self, subject: str, runner: str) -> dict:
        """Pin ONE subject to a harness, through admin-api's per-user model config.

        `PUT /admin/users/{id}/models`, not `PUT /user/models`: the self-serve route takes the
        caller's own identity, and this caller is binding a config to somebody else. And not the
        platform setting either — that would change the model for every person on the instance,
        which is the exact opposite of what a rehearsal is for.
        """
        cfg = runner_config(runner)
        st, body = _http("PUT", f"{ADMIN_API}/admin/users/{subject}/models", self._ak(), cfg)
        if st in (404, 405):
            raise DoorRefused(
                f"admin-api has no PUT /admin/users/{{id}}/models on the running image ({st}). "
                "The route ships on this branch; the deployment needs the admin-api swap before a "
                "runner can be pinned per subject. Nothing was bound.")
        if not 200 <= st < 300:
            raise DoorRefused(f"admin-api refused the runner binding for {subject}: "
                              f"{st} {str(body)[:200]}")
        return {"subject": str(subject), "runner": runner, "config": cfg}

    def cancel_bot_leg(self, flow: str, source_contains: str = "") -> dict:
        """Cancel this recipe's own parked reaction — `POST /reactions/{id}/cancel`, the product's
        audited lifecycle verb.

        A REHEARSAL MUST LEAVE NOTHING ARMED. `invite_intake` parks on `await_start` until
        start−2min and then dispatches a REAL bot at the invite's URL, and the states that use an
        invite are rehearsing the PREPARE TOUCH — the bot leg is not what they measure. Leaving the
        reaction parked means the run has armed a live dispatch at a fixture Zoom URL that fires on
        the clock, long after the state was reported green. It happened: meeting 115 reached
        `joining` at 19:20Z while the catalogue was still running.

        Scoped by `source_contains` so it can only reach a reaction this recipe's own derived ids
        name — never another lane user's parked work.
        """
        st, body = _http("GET", f"{FLOWS_API}/reactions?limit=100",
                         {"X-Flows-Admin-Key": self._flows_key})
        rows = (body or {}).get("reactions", []) if isinstance(body, dict) else []
        if st != 200 or not isinstance(body, dict):
            raise DoorRefused(f"could not list reactions to cancel the bot leg: {st}")
        targets = [r for r in rows
                   if r.get("flow") == flow
                   and str(r.get("status")) in ("admitted", "retrying")
                   and (not source_contains
                        or source_contains in str(r.get("source_event_id") or ""))]
        cancelled, refused = [], []
        for r in targets:
            rid = r.get("reaction_id") or r.get("id")
            cst, cb = _http("POST", f"{FLOWS_API}/reactions/{rid}/cancel",
                            {"X-Flows-Admin-Key": self._flows_key}, {})
            (cancelled if 200 <= cst < 300 else refused).append(f"{rid}:{cst}")
        if refused:
            raise DoorRefused(
                f"cancelled {len(cancelled)}, REFUSED {refused} — a parked invite reaction left "
                f"behind will dispatch a real bot at a fixture URL when its clock arrives")
        return {"flow": flow, "cancelled": len(cancelled), "ids": cancelled}

    # ── reads ─────────────────────────────────────────────────────────────────────────────────
    def user_find(self, address: str):
        st, u = _http("GET", f"{ADMIN_API}/admin/users/email/{urllib.parse.quote(address)}",
                      self._ak())
        return str(u["id"]) if st == 200 and isinstance(u, dict) and u.get("id") else None

    def user_email(self, uid: str) -> str:
        st, u = _http("GET", f"{ADMIN_API}/admin/users/{uid}", self._ak())
        if st != 200 or not isinstance(u, dict):
            raise DoorRefused(f"no user {uid} on this instance ({st})")
        return str(u.get("email") or "")

    def meeting_get(self, owner: str, meeting_id) -> dict:
        st, b = self._gw(owner, "GET", f"/meetings/{meeting_id}")
        if st == 200 and isinstance(b, dict):
            return b
        row = self._find_meeting(owner, meeting_id)
        if row:
            return row
        raise DoorRefused(f"meeting {meeting_id} is not readable as uid {owner} ({st})")

    def desk_tree(self, subject: str, slug: str = "") -> list:
        """One desk's files, READ AS `subject`. `slug` selects a group desk they belong to.

        The reader is always a person: agent-api resolves a group by membership, so asking for a
        group desk without saying who is asking is a question the product cannot answer — and the
        404 it would return reads as "the desk is empty", which is the wrong finding entirely."""
        url = f"{AGENT_API}/api/workspace/tree"
        if slug:
            url += f"?slug={urllib.parse.quote(str(slug))}"
        st, b = _http("GET", url, {"X-User-Id": str(subject)})
        if st != 200 or not isinstance(b, dict):
            raise DoorRefused(f"could not read the desk {slug or subject} as {subject}: {st}")
        files = b.get("files") or b.get("tree") or []
        return [f if isinstance(f, str) else (f.get("path") or "") for f in files]

    def group_members(self, owner: str, group: str) -> list:
        st, r = _http("GET", f"{AGENT_API}/api/workspace/members?workspace_id={urllib.parse.quote(group)}",
                      {"X-User-Id": str(owner)})
        if st != 200 or not isinstance(r, dict):
            return []
        return list(r.get("members") or [])

    def scaffold_get(self, scaffold_id: str, subject: str = "") -> dict:
        hdr = {"X-Internal-Secret": _internal_secret()} if not subject else {"X-User-Id": str(subject)}
        st, b = _http("GET", f"{AGENT_API}/api/scaffolds/{scaffold_id}", hdr)
        if st != 200 or not isinstance(b, dict):
            raise DoorRefused(
                f"scaffold {scaffold_id} does not resolve ({st}). A link whose record cannot be "
                f"read opens onto nothing — that is the defect, not the check.")
        return b

    # ── the guard ─────────────────────────────────────────────────────────────────────────────
    LIVE_STATUSES = ("active", "joining", "requested", "awaiting_admission", "needs_help",
                     "stopping")

    def live_meetings(self) -> list:
        """Every live meeting on the stack, with the owner's address.

        READ-ONLY, and through the postgres container because there is no instance-wide route
        (`GET /bots/status` is scoped to one caller). It FAILS CLOSED: an unreadable probe raises,
        and `rehearse()` refuses. The guard exists so a rehearsal can never run beside somebody's
        real meeting, and a guard that degrades to "probably fine" is not one.
        """
        sql = ("SELECT m.id, m.status, u.email FROM meetings m JOIN users u ON u.id = m.user_id "
               f"WHERE m.status IN ({', '.join(chr(39) + s + chr(39) for s in self.LIVE_STATUSES)});")
        r = subprocess.run(
            ["docker", "exec", f"{self.stack}-postgres-1", "psql", "-U", "postgres", "-d", "vexa",
             "-tAF", "|", "-c", sql], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise DoorRefused(
                "could not read the live-meeting probe, so the rehearsal refuses rather than "
                "running blind beside a real meeting: " + (r.stderr or "").strip()[:300])
        out = []
        for line in r.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) == 3:
                out.append({"id": parts[0], "status": parts[1], "email": parts[2].lower()})
        return out

    # ── the reset ─────────────────────────────────────────────────────────────────────────────
    def user_delete(self, uid: str) -> dict:
        st, b = _http("DELETE", f"{ADMIN_API}/admin/users/{uid}", self._ak())
        if st in (200, 204):
            return {"deleted": True, "via": "admin-api"}
        if st in (404, 405):
            raise DoorRefused(
                f"admin-api has no DELETE /admin/users/{{id}} on the running image ({st}). "
                "The route exists on this branch; the deployment needs the admin-api swap before "
                "subject_reset can remove a user. Nothing was deleted.")
        raise DoorRefused(f"admin-api refused to delete uid {uid}: {st} {str(b)[:200]}")

    def meetings_delete_for(self, subject: str) -> int:
        """Every meeting this subject owns, deleted through the product's own `DELETE /meetings/{id}`.

        Needed on its own, not only as part of the user delete: a run that fails BETWEEN
        `POST /meetings` and the transcript import leaves a non-terminal row, and the next attempt
        at that state is refused — correctly — with `409 a non-terminal meeting already holds this
        native id`. Found live on run 2, where three states could not be re-entered for that
        reason. Without this the only way out was a hand-written call.
        """
        rows = self._meetings_of(subject)
        gone, refused = 0, []
        for row in rows:
            dst, body = self._gw(subject, "DELETE", f"/meetings/{row.get('id')}")
            if dst in (200, 204):
                gone += 1
            else:
                refused.append(f"{row.get('id')}:{dst}")
        if refused:
            # A delete that did not happen must not be counted as one. The caller reports this
            # under `remaining`, and the next run of that state would otherwise meet a 409 it
            # cannot explain.
            raise DoorRefused(f"deleted {gone} meeting(s); refused: {', '.join(refused)}")
        return gone

    def desk_delete(self, subject: str) -> dict:
        st, b = _http("DELETE", f"{AGENT_API}/api/workspace/{urllib.parse.quote(str(subject))}",
                      {"X-User-Id": str(subject)})
        if st in (200, 204, 404):
            return {"deleted": st != 404, "status": st}
        # The baseline desk refuses DELETE by design; reset is its delete-equivalent.
        st2, b2 = _http("POST", f"{AGENT_API}/api/workspace/reset", {"X-User-Id": str(subject)},
                        {"target": "personal"})
        if st2 in (200, 201):
            return {"deleted": False, "reset": True, "status": st2}
        raise DoorRefused(f"could not remove the desk of {subject}: {st} {str(b)[:150]} / "
                          f"{st2} {str(b2)[:150]}")

    def session_keys_delete(self, subject: str) -> int:
        """`agent:sessions:<uid>` and every `agent:session:<uid>:*` it indexes. NOTHING ELSE.

        The prefixes are listed, never globbed on `agent:*`: a list is reviewable and a glob
        silently adopts whatever the next feature names. The same valkey carries `tc:meeting:*`
        live transcript streams, which is why no FLUSH appears anywhere in this file.
        """
        return self._redis_del([f"agent:sessions:{subject}", f"agent:session:{subject}:*"])

    def scaffold_keys_delete(self, address: str) -> int:
        """That recipient's pending-scaffold index, and each record it names.

        A scaffold outliving a reset is a live capability minted for an address — the reason it is
        cleared rather than left to expire.
        """
        idx = f"agent:scaffolds:by:{_recipient_key(address)}"
        ids = self._redis(["SMEMBERS", idx]).split()
        keys = [idx] + [f"agent:scaffold:{i.strip()}" for i in ids if i.strip()]
        return self._redis_del(keys)

    def _redis_del(self, patterns: list[str]) -> int:
        cli = self._redis_cli()
        n = 0
        for pat in patterns:
            if "*" in pat:
                out = self._redis(["--scan", "--pattern", pat, "--count", "500"])
                keys = [k for k in out.split() if k.strip()]
            else:
                keys = [pat] if self._redis(["EXISTS", pat]).strip() == "1" else []
            for k in keys:
                self._redis(["DEL", k])
                n += 1
        del cli
        return n

    def _redis_cli(self) -> str:
        r = subprocess.run(["docker", "exec", f"{self.stack}-redis-1", "sh", "-lc",
                            "command -v valkey-cli || command -v redis-cli"],
                           capture_output=True, text=True, timeout=20)
        cli = (r.stdout or "").strip()
        if not cli:
            raise DoorRefused(f"no valkey-cli/redis-cli in {self.stack}-redis-1 — a pending "
                              "scaffold would survive the reset, and a scaffold is a capability")
        return cli

    def _redis(self, args: list[str]) -> str:
        cli = getattr(self, "_cli_cache", "") or self._redis_cli()
        self._cli_cache = cli
        r = subprocess.run(["docker", "exec", f"{self.stack}-redis-1", cli, *args],
                           capture_output=True, text=True, timeout=60)
        return r.stdout or ""

    #: The lane tables that hold ONE PERSON'S rows, in FK order — receipts and signals reference a
    #: reaction, so the reaction goes last. The same order `blank-instance.sh` documents, and the
    #: same list minus the lane-wide ones (`mail_cursor` is the poller's watermark and belongs to
    #: the deployment, not to a subject; deleting it would replay the whole box).
    LANE_TABLES = ("effect_receipt", "signal", "reaction", "mail_thread", "mail_outbox_sent")

    def lane_rows_delete_for(self, subject: str, address: str) -> dict:
        """One subject's rows in every flows lane. THIS is what makes a state re-enterable.

        `admit()` dedups on (source_event_id, flow), and a rehearsal's source ids are derived from
        (state, subject, meeting) precisely so a re-run is idempotent. The other side of that coin:
        after a reset the SAME ids must be admissible again, and they are not while the earlier
        reaction is still in the lane. Found live — the poller logged `admitted 0` for a fresh
        invite, no prepare mail was sent, and the step waited out its whole budget for a touch
        nothing was ever going to produce.

        MATCHED ON THE SUBJECT, never on a bare number: `"uid": "13"` must not take uid 130 with
        it. Both JSON spellings, plus the address, which is how the invite lineage names a person.
        """
        out: dict = {}
        for db in self._flow_lanes():
            for table in self.LANE_TABLES:
                where = self._lane_where(table, str(subject), address)
                if not where:
                    continue
                r = subprocess.run(
                    ["docker", "exec", f"{self.stack}-postgres-1", "psql", "-U", "postgres",
                     "-d", db, "-tAc", f"DELETE FROM {table} WHERE {where};"],
                    capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    if "does not exist" in (r.stderr or ""):
                        continue                     # lanes differ; absent is not a failure
                    raise DoorRefused(
                        f"could not clear {db}.{table} for {address}: "
                        f"{(r.stderr or '').strip()[:200]}. Refusing to report a reset that would "
                        f"leave this state un-re-enterable.")
                n = _int((r.stdout or "").replace("DELETE", ""))
                if n:
                    out[f"{db}.{table}"] = n
        return out

    @staticmethod
    def _lane_where(table: str, subject: str, address: str) -> str:
        """The per-subject predicate for one lane table, or "" when the table names no person —
        in which case it is left alone rather than guessed at."""
        def q(v: str) -> str:
            return "'" + str(v).replace("'", "''") + "'"

        # MATCHED ON THE SUBJECT, never on a bare number: `%13%` would take uid 130 with it. Both
        # JSON spellings of the key, plus the address, which is how the invite lineage names a
        # person before any uid exists.
        naming = " OR ".join(
            f"{{col}} LIKE {q('%' + pat + '%')}"
            for pat in (f'"uid": "{subject}"', f'"uid":"{subject}"', address))
        if table in ("effect_receipt", "signal"):
            inner = naming.format(col="r.subject_refs")
            return f"reaction_id IN (SELECT reaction_id FROM reaction r WHERE {inner})"
        if table == "reaction":
            return naming.format(col="subject_refs")
        if table in ("mail_thread", "mail_outbox_sent"):
            return f"subject_uid = {q(subject)}"
        return ""

    def friction_delete_for(self, subject: str) -> int:
        n = 0
        for db in self._flow_lanes():
            r = subprocess.run(
                ["docker", "exec", f"{self.stack}-postgres-1", "psql", "-U", "postgres", "-d", db,
                 "-tAc", f"DELETE FROM friction WHERE subject = '{subject}';"],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                n += _int(r.stdout.replace("DELETE", ""))
        return n

    def _flow_lanes(self) -> list[str]:
        r = subprocess.run(
            ["docker", "exec", f"{self.stack}-postgres-1", "psql", "-U", "postgres", "-tAc",
             "SELECT datname FROM pg_database WHERE datname LIKE 'flows%' ORDER BY 1;"],
            capture_output=True, text=True, timeout=30)
        return [x.strip() for x in (r.stdout or "").split() if x.strip()]

    def mail_delete_for(self, address: str) -> int:
        n = 0
        for q in (f"to:{address}", f"from:{address}"):
            st, body = _http("GET", f"{MAILPIT}/api/v1/search?query={urllib.parse.quote(q)}&limit=500",
                             None)
            ids = [m["ID"] for m in ((body or {}).get("messages") or [])] if isinstance(body, dict) else []
            if ids:
                _http("DELETE", f"{MAILPIT}/api/v1/messages", None, {"IDs": ids})
                n += len(ids)
        return n


# ── small helpers ────────────────────────────────────────────────────────────────────────────────

_LINK = re.compile(r"https?://[^\s\"'<>)\]]+")


def _links(text: str) -> list[str]:
    seen, out = set(), []
    for m in _LINK.finditer(text or ""):
        u = m.group(0).rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _mail_epoch(msg: dict) -> float | None:
    """When mailpit says this message was created, or None when the stamp cannot be read.

    NONE IS NOT ZERO, and that distinction cost a whole run. This used to return 0.0 on a stamp it
    could not parse, and the caller's filter was `_mail_epoch(msg) < since`, so an unreadable
    timestamp meant "older than this run" — every message rejected. Run 4 failed three states with
    the message *"no mail whose subject contains 'Prepare' arrived within 180s. 2 message(s) to
    that address exist: Accepted: …; Prepare: …"* — naming, in its own refusal, the mail it had
    just thrown away.

    It is the same defect as the 422 read as an empty list, one layer down: a parse that fails is
    not an answer, and code that turns it into one produces a confident wrong result. A stamp we
    cannot read now means "I cannot place this message in time", and the caller decides — it
    includes the message rather than dropping it, because a false accept is a check that needs
    tightening while a false reject is a touch that never happened.

    Parsed with `datetime.fromisoformat`, which takes mailpit's RFC3339 (Go trims trailing zeros
    from the fraction, so `.5Z` sits next to `.503Z` — the old fixed-width slicing could not).
    """
    raw = str(msg.get("Created") or "").strip()
    if not raw:
        return None
    import datetime as _dt
    text = raw.replace("Z", "+00:00")
    try:
        return _dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    # Pre-3.11 spellings and over-long fractions: trim the fraction to microseconds and retry.
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", text)
    if m:
        frac = (m.group(2) or "")[:7]
        try:
            return _dt.datetime.fromisoformat(m.group(1) + frac + (m.group(3) or "")).timestamp()
        except ValueError:
            return None
    return None


def _int(s: str) -> int:
    try:
        return int(str(s).strip())
    except ValueError:
        return 0


def _recipient_key(address: str) -> str:
    """The index key agent-api writes a recipient under. Mirrors scaffolds._recipient_key."""
    import hashlib
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()[:32]


def _admin_key(stack: str = STACK) -> str:
    key = (os.environ.get("VEXA_ADMIN_API_TOKEN") or "").strip()
    if key:
        return key
    r = subprocess.run(
        ["docker", "inspect", f"{stack}-admin-api-1", "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"], capture_output=True, text=True)
    if r.returncode != 0 or "ADMIN_API_TOKEN=" not in r.stdout:
        raise DoorRefused("no admin-api token: set VEXA_ADMIN_API_TOKEN, or run where the "
                          f"{stack}-admin-api-1 container is inspectable")
    return r.stdout.split("ADMIN_API_TOKEN=")[1].split("\n")[0].strip()


def _flows_key() -> str:
    key = (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip()
    if key:
        return key
    for cand in ("flows-api-key", "sim-flows-api-key"):
        f = pathlib.Path.home() / ".storm" / cand
        if f.is_file() and f.read_text().strip():
            return f.read_text().strip()
    raise DoorRefused("no flows-api key: set VEXA_FLOWS_API_KEY or place ~/.storm/flows-api-key")


def _internal_secret() -> str:
    # ONE name (F95): INTERNAL_API_SECRET, the compose/helm secret key. The two prefixed spellings
    # are read after it so a shell mid-upgrade still works; this file papering over the drift in one
    # place is what let it survive everywhere else.
    key = (os.environ.get("INTERNAL_API_SECRET")
           or os.environ.get("VEXA_INTERNAL_SECRET")
           or os.environ.get("VEXA_INTERNAL_API_SECRET") or "").strip()
    if key:
        return key
    f = pathlib.Path.home() / ".storm/internal-secret"
    if f.is_file():
        return f.read_text().strip()
    raise DoorRefused("no internal secret: set INTERNAL_API_SECRET or place "
                      "~/.storm/internal-secret (mode 600)")
