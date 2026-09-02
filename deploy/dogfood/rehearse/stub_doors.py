"""StubDoors — the whole stack as a dict, so every recipe is provable with nothing running.

It is NOT a mock in the usual sense: it does not record calls for an assertion to inspect. It is a
tiny model of the product's own behaviour at each door — an invite produces an RSVP and a prepare
mail, a completed meeting produces an attendee mail, a mail carries a `/?s=` link whose scaffold
resolves — so `run_all.py --stub` exercises the SAME code path the live run takes and fails on the
same verify block. The offline proof is therefore about the recipes and the engine, and only the
doors are substituted.

What it deliberately does NOT model: agent turns, timing, and whether the words in a mail are any
good. Those need the stack and, for the last one, a person.
"""
from __future__ import annotations

import re
import time

from .doors import DoorRefused, Doors, runner_config

UI = "https://app.dev.vexa.test"


class StubDoors(Doors):
    def __init__(self, *, blank: bool = True, live: list | None = None):
        self.users: dict[str, str] = {}
        self.desks: dict[str, list] = {}
        self.groups: dict[str, dict] = {}
        self.meetings: dict[str, dict] = {}
        self.mail: list[dict] = []
        self.scaffolds: dict[str, dict] = {}
        self.facts: list[dict] = []
        self.reactions: list[dict] = []
        self.runners: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self._blank = blank
        self._live = list(live or [])
        self._n = 0

    # -- helpers ---------------------------------------------------------------------------------
    def _id(self, prefix: str = "") -> str:
        self._n += 1
        return f"{prefix}{self._n:06d}"

    def _scaffold(self, kind: str, who: str, refs: dict | None = None) -> str:
        sid = self._id("sc")
        self.scaffolds[sid] = {"id": sid, "kind": kind, "who": who, "refs": refs or {}}
        return f"{UI}/?s={sid}"

    def _send(self, to: str, subject: str, body: str) -> dict:
        msg = {"id": self._id("m"), "subject": subject, "to": [to], "from": "vexa@storm.test",
               "message_id": f"<{self._id('mid')}@rehearse.local>", "text": body, "html": "",
               "body": body, "links": re.findall(r"https?://\S+", body), "at": time.time()}
        self.mail.append(msg)
        return msg

    # -- verbs -----------------------------------------------------------------------------------
    def require_instance_blank(self) -> dict:
        self.calls.append(("require_instance_blank",))
        if not self._blank:
            # Word for word the live refusal (`LiveDoors.require_instance_blank`): a double whose
            # error text differs from the door's teaches a caller to handle a message the stack
            # never sends.
            raise DoorRefused(
                "the instance is NOT blank: an admin has claimed it and the company layer is "
                "completed. `blank-admin` asserts this state, it never creates it — blanking "
                "deletes every person on the stack and is `bin/blank-instance.sh`, run on purpose.")
        return {"blank": True}

    def require_subject_absent(self, address: str) -> dict:
        self.calls.append(("require_subject_absent", address))
        if address in self.users:
            raise DoorRefused(f"{address} already has a user (uid {self.users[address]})")
        return {"absent": True, "address": address}

    def user_ensure(self, address: str) -> dict:
        existed = address in self.users
        uid = self.users.setdefault(address, self._id())
        self.calls.append(("user_ensure", address))
        return {"uid": uid, "email": address, "existed": existed}

    def desk_init(self, subject: str) -> dict:
        self.desks.setdefault(str(subject), ["README.md"])
        self.calls.append(("desk_init", subject))
        return {"subject": str(subject), "status": 201}

    def desk_entity(self, subject: str, kind: str, name: str, facts=(), source: str = "",
                    summary: str = "", slug: str = "") -> dict:
        path = f"kg/entities/{kind}/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.md"
        self.desks.setdefault(str(slug or subject), []).append(path)
        self.calls.append(("desk_entity", subject, kind, name))
        return {"path": path, "created": True, "changed": True}

    def group_new(self, owner: str, name: str, purpose: str = "") -> dict:
        wid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + self._id()
        self.groups[wid] = {"owner": str(owner), "name": name, "members": [
            {"subject": str(owner), "role": "owner", "email": ""}]}
        self.desks.setdefault(wid, ["README.md"])
        self.calls.append(("group_new", owner, name))
        return {"workspace_id": wid, "name": name, "owner": str(owner)}

    def group_join(self, group: str, owner: str, member: str, member_email: str = "",
                   role: str = "contributor") -> dict:
        g = self.groups.get(group)
        if not g:
            raise DoorRefused(f"no such group {group}")
        g["members"].append({"subject": str(member), "role": role, "email": member_email})
        self.calls.append(("group_join", group, member))
        return {"group": group, "member": str(member), "joined": True}

    def request_sign_in_link(self, address: str) -> dict:
        self._send(address, "Sign in to Vexa",
                   f"Your sign-in link:\n{UI}/auth/redeem?t={self._id('tok')}\n")
        self.calls.append(("request_sign_in_link", address))
        return {"requested": address, "status": 200}

    def drop_invite(self, organizer: str, title: str, start: float, attendees=(),
                    ics_uid: str = "", group: str = "", url: str = "") -> dict:
        """The invite, and everything `invite_intake` does with one — modelled, not faked.

        ensure_user · rsvp_accept · ack_by_email · emit_prep. `await_start` parks (start is in the
        future by construction), so no bot is dispatched and no meeting completes here.
        """
        uid = self.users.setdefault(organizer, self._id())
        self.desks.setdefault(uid, ["README.md"])
        self._send(organizer, f"Accepted: {title}", "Vexa will be there.")
        link = self._scaffold("prep", organizer,
                              {"title": title, "when": start, "organizer": organizer,
                               "state": {"desk": self._desk_state(uid), "group": "absent"}})
        self._send(organizer, f"Prepare: {title}",
                   f"{title} — I'll be in the room. Open the chat: {link}\n")
        mid = self._id()
        self.meetings[mid] = {"id": mid, "native_meeting_id": ics_uid or mid, "status": "scheduled",
                              "owner": uid, "title": title}
        self.calls.append(("drop_invite", organizer, title, group))
        return {"ics_uid": ics_uid, "to": "vexa@storm.test", "organizer": organizer,
                "start": start, "attendees": [a for _, a in attendees]}

    #: `transcript-import`'s own vocabulary, from its 422. A double that accepts what the real
    #: route refuses certifies a broken caller — which is exactly what happened on the first live
    #: run: three states passed here and 422'd there.
    IMPORT_SOURCES = ("import", "seed")

    def seed_meeting(self, owner: str, native: str, title: str, segments: list,
                     started_at: float, source: str = "seed") -> dict:
        if source not in self.IMPORT_SOURCES:
            raise DoorRefused(
                f"'source' must be one of {list(self.IMPORT_SOURCES)} — say where the transcript "
                f"came from (got {source!r})")
        for row in self.meetings.values():            # adopt, exactly as LiveDoors does
            if row["native_meeting_id"] == native and row["status"] == "completed":
                return {"meeting_id": row["id"], "native_meeting_id": native, "platform": "jitsi",
                        "status": "completed", "segments_loaded": len(segments),
                        "imported": False, "started_epoch": int(started_at),
                        "ended_epoch": int(started_at)}
        mid = self._id()
        row = {"id": mid, "native_meeting_id": native, "platform": "jitsi", "status": "completed",
               "owner": str(owner), "title": title}
        self.meetings[mid] = row
        self.calls.append(("seed_meeting", owner, native))
        return {"meeting_id": mid, "native_meeting_id": native, "platform": "jitsi",
                "status": "completed", "segments_loaded": len(segments), "imported": True,
                "started_epoch": int(started_at),
                "ended_epoch": int(started_at + max((s["end"] for s in segments), default=0))}

    def emit_fact(self, event_type: str, source_event_id: str, refs: dict) -> dict:
        dup = any(f["source_event_id"] == source_event_id for f in self.facts)
        self.facts.append({"event_type": event_type, "source_event_id": source_event_id,
                           "refs": refs})
        self.calls.append(("emit_fact", event_type, source_event_id))
        if event_type == "meeting.completed" and not dup:
            self._post_meeting(refs)
        return {"event_type": event_type, "source_event_id": source_event_id,
                "reactions_created": 0 if dup else 1, "duplicate": dup, "at": time.time()}

    def _post_meeting(self, refs: dict) -> None:
        """`post_meeting`: one shared report, one mail per attendee, one drop per desk.

        Decision 22a — the organizer's desk always receives the drop, even when the room is all
        external. Modelled because a state's verify block asks about it.
        """
        title = str(refs.get("title") or "Meeting")
        group = str(refs.get("group") or "")
        everyone = list(dict.fromkeys([str(refs.get("organizer") or "")]
                                      + list(refs.get("participants") or [])))
        for who in [w for w in everyone if w]:
            uid = self.users.get(who) or self.users.setdefault(who, self._id())
            self.desks.setdefault(uid, []).append(f"kg/entities/meeting/{title}.md")
            link = self._scaffold("post-meeting", who,
                                  {"meeting": refs.get("meeting_id"), "group": group or None})
            self._send(who, f"{title} — what it means for you",
                       f"The report from {title}.\nOpen the chat: {link}\n")
        if group and group in self.groups:
            self.desks.setdefault(group, []).append(f"kg/entities/meeting/{title}.md")
        self.reactions.append({"flow": "post_meeting", "state": "done", "id": self._id("r"),
                               "created_at": time.time()})

    def await_mail(self, to: str, subject_contains: str = "", budget_s: int = 180,
                   since: float = 0.0) -> dict:
        for msg in reversed(self.mail):
            if to not in msg["to"]:
                continue
            if subject_contains and subject_contains.lower() not in msg["subject"].lower():
                continue
            if since and msg["at"] < since - 5:
                continue          # a previous run's touch is not this run's evidence
            return dict(msg)
        raise DoorRefused(f"no mail to {to} containing {subject_contains!r}")

    def reply_to_mail(self, message: dict, from_address: str, body: str) -> dict:
        orig = str(message.get("message_id") or "")
        if not orig:
            raise DoorRefused("the mail we are replying to carries no Message-ID")
        self.reactions.append({"flow": "email_chat", "state": "running", "id": self._id("r"),
                              "created_at": time.time()})
        self.calls.append(("reply_to_mail", from_address))
        return {"in_reply_to": orig, "message_id": self._id("mid"), "from": from_address,
                "sent_at": time.time()}

    def await_reaction(self, flow: str, since: float = 0.0, budget_s: int = 300) -> dict:
        for r in reversed(self.reactions):
            if r["flow"] == flow:
                return {"flow": flow, "state": r["state"], "id": r["id"], "reaction": r,
                        "admitted": True}
        raise DoorRefused(f"no `{flow}` reaction appeared")

    def bind_runner(self, subject: str, runner: str) -> dict:
        cfg = runner_config(runner)                     # the same refusal the live door raises
        self.runners[str(subject)] = cfg
        self.calls.append(("bind_runner", subject, runner))
        return {"subject": str(subject), "runner": runner, "config": cfg}

    def _desk_state(self, subject: str) -> str:
        """`new` | `pile` | `warm`, by the product's rule (`control_plane/scaffolds.desk_state`):
        meeting entities ALONE are a pile — reports landed and nobody wired them — and it takes a
        non-meeting entity for somebody to have worked here. Mirrored rather than invented, because
        the recipe asserts against it and a double with its own opinion proves nothing."""
        files = [f for f in self.desks.get(str(subject), []) if "kg/entities/" in f]
        if not files:
            return "new"
        other = [f for f in files if "/meeting/" not in f and not f.endswith("index.md")]
        return "warm" if other else "pile"

    # -- reads -----------------------------------------------------------------------------------
    def user_find(self, address: str):
        return self.users.get(address)

    def meeting_get(self, owner: str, meeting_id) -> dict:
        return self.meetings[str(meeting_id)]

    def desk_tree(self, subject: str, slug: str = "") -> list:
        return list(self.desks.get(str(slug or subject), []))

    def group_members(self, owner: str, group: str) -> list:
        return list((self.groups.get(group) or {}).get("members") or [])

    def scaffold_get(self, scaffold_id: str, subject: str = "") -> dict:
        if scaffold_id not in self.scaffolds:
            raise DoorRefused(f"scaffold {scaffold_id} does not resolve (404)")
        return self.scaffolds[scaffold_id]

    # -- guard + reset ---------------------------------------------------------------------------
    def live_meetings(self) -> list:
        return list(self._live)

    def user_delete(self, uid: str) -> dict:
        """Deleting a user takes their meetings with them — the FK cascade admin-api's route
        performs. Modelled here because `rehearse(..., fresh=True)` depends on it: the fact's id
        names the meeting row, so a row that outlived its owner would dedup the re-entry away."""
        for addr, u in list(self.users.items()):
            if u == str(uid):
                del self.users[addr]
        for mid, row in list(self.meetings.items()):
            if row.get("owner") == str(uid):
                del self.meetings[mid]
        return {"deleted": True, "via": "admin-api"}

    def meetings_delete_for(self, subject: str) -> int:
        gone = [m for m, row in self.meetings.items() if row.get("owner") == str(subject)]
        for m in gone:
            del self.meetings[m]
        return len(gone)

    def desk_delete(self, subject: str) -> dict:
        return {"deleted": self.desks.pop(str(subject), None) is not None}

    def session_keys_delete(self, subject: str) -> int:
        return 0

    def scaffold_keys_delete(self, address: str) -> int:
        gone = [k for k, v in self.scaffolds.items() if v["who"] == address]
        for k in gone:
            del self.scaffolds[k]
        return len(gone)

    def friction_delete_for(self, subject: str) -> int:
        return 0

    def mail_delete_for(self, address: str) -> int:
        before = len(self.mail)
        self.mail = [m for m in self.mail if address not in m["to"] and m["from"] != address]
        return before - len(self.mail)
