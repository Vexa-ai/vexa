"""mailkinds.py — THE CATALOGUE OF MAIL THIS DEPLOYMENT CAN SEND, one row per kind.

Founder, 2026-10-14: *"the actual notifications should be decided by the global account, so they
design and test, starting from templates we provide."* The mechanism is ours; WHAT is sent, WHEN and
IN WHICH WORDS is the global (admin) account's. So every mail that leaves the system has a KIND, and a
kind has:

  * a starter template (`mailtext.DEFAULTS[template]`, overridable at `_global/mail/<template>.md`),
  * the variables a template may use, each with a sample value for `mail_preview`,
  * a switch in `_global/POLICIES.md`: `mail.<kind>: on | off | admins`,
  * a default for that switch — conservative: the sign-in link is always on, sharing and call
    invites are on (a person pressed a button for each of them), everything else is OFF until the
    global account designs it, test-sends it and turns it on.

`admins` means: sent only when the recipient is the deployment's admin account — a way to live with a
new kind for a while before everybody gets it.

`locked` kinds cannot be switched off: the sign-in link is how a person gets in, and a policy file
must never be able to lock the admin out of the deployment that would let them fix it. The template
stays editable.

`producer` says which code sends the kind today. A kind with no producer is a template ready for a
flow to use (`flows_steps.outbound.send(kind, …)`), not a promise that something sends it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ON, OFF, ADMINS = "on", "off", "admins"
SWITCH_VALUES = (ON, OFF, ADMINS)

#: The policy key a kind's switch lives under.
def policy_key(kind: str) -> str:
    return f"mail.{kind}"


#: Common variables every template may use — `mailtext.render` fills them itself.
COMMON_VARIABLES: dict[str, str] = {
    "company": "the company name, the first heading of _global/README.md",
    "service": "the fixed sentence that says what Vexa does",
    "visibility": "who can see what, derived from _global/POLICIES.md",
    "workspace": "the product's word for a person's own workspace",
    "mailbox": "the address people add to their meetings",
}


@dataclass(frozen=True)
class Kind:
    name: str
    title: str
    group: str                      # system · invite · notice · report
    default: str                    # on · off · admins
    template: str | None            # the mailtext template name; None = fixed protocol text
    variables: dict = field(default_factory=dict)   # name -> what it is
    sample: dict = field(default_factory=dict)      # name -> sample value for mail_preview
    producer: str = ""              # the code that sends it today ("" = none yet)
    locked: bool = False            # the switch cannot be turned off
    link: bool = False              # the sender appends one link (the call to action)


_SAMPLE_MEETING = {"title": "Weekly product sync", "when": "Thu 16 Oct, 10:00 Lisbon",
                   "meeting": "Weekly product sync", "date": "16 Oct 2026",
                   "organizer": "Anna Example", "url": "https://meet.google.com/abc-defg-hij"}

KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind("sign-in", "Sign-in link", "system", ON, "sign-in",
         {"minutes": "how many minutes the link stays valid"},
         {"minutes": "15"},
         "terminal /api/auth/request-link → flows-api POST /mail/sign-in", locked=True, link=True),
    Kind("workspace-invite", "Workspace share invite", "invite", ON, "workspace-invite",
         {"inviter": "who invited them", "workspace_name": "the workspace's display name",
          "role": "the role they were given", "role_sentence": "what that role may do"},
         {"inviter": "Anna Example", "workspace_name": "Sales desk", "role": "member",
          "role_sentence": "Members read and write the workspace's pages."},
         "flows production_agent.mail_workspace_invite (workspace.invited)", link=True),
    Kind("workspace-joined", "Someone joined via your invite link", "invite", ON, "workspace-joined",
         {"joiner": "who joined (name, else address)", "workspace_name": "the workspace's name"},
         {"joiner": "Anna Example", "workspace_name": "Sales desk"},
         "flows-api POST /mail/send (the invite-link redemption)", link=True),
    Kind("meeting-share", "Meeting/call share invite", "invite", ON, "meeting-share",
         {"inviter": "who shared it", "title": "the meeting or chat title"},
         {"inviter": "Anna Example", "title": "Weekly product sync"},
         "flows production.mail_meeting_share (meeting.shared)", link=True),
    Kind("chat-share", "Chat share invite", "invite", ON, "chat-share",
         {"inviter": "who shared it", "title": "the chat's title"},
         {"inviter": "Anna Example", "title": "Pricing questions"},
         "", link=True),
    Kind("call-invite", "Call invite", "invite", ON, "call-invite",
         {"inviter": "who is calling", "title": "the call's title", "when": "when it starts"},
         {"inviter": "Anna Example", "title": "Quick call", "when": "now"},
         "", link=True),
    Kind("bot-scheduled", "Bot scheduled", "notice", OFF, "bot-scheduled",
         {"title": "the meeting title", "url": "the meeting link", "when": "start, their clock"},
         _SAMPLE_MEETING, "flows production.ack_by_email (invite.received)"),
    Kind("bot-joined", "Bot joined", "notice", OFF, "bot-joined",
         {"title": "the meeting title", "url": "the meeting link"},
         _SAMPLE_MEETING, "", link=True),
    Kind("bot-failed", "Bot could not join", "notice", OFF, "bot-failed",
         {"title": "the meeting title", "url": "the meeting link", "reason": "why, in one line"},
         {**_SAMPLE_MEETING, "reason": "nobody admitted the bot from the lobby"}, ""),
    Kind("prepare", "Prepare for a meeting", "notice", OFF, "prepare",
         {"title": "the meeting title", "when": "start, their clock"},
         _SAMPLE_MEETING, "flows production_agent.emit_prep (prepare step)", link=True),
    Kind("minutes", "Minutes", "report", OFF, "minutes-head",
         {"title": "the meeting title", "when": "start, their clock"},
         _SAMPLE_MEETING, "flows production.email_minutes (meeting.completed)", link=True),
    Kind("attendee-report", "Report to attendees", "report", OFF, "attendee-head",
         {"meeting": "the meeting title", "organizer": "who had Vexa in the room",
          "date": "the meeting date"},
         _SAMPLE_MEETING, "flows production.email_attendees (meeting.completed)", link=True),
    Kind("recording", "Recording ready", "report", OFF, "recording",
         {"title": "the meeting title"}, _SAMPLE_MEETING,
         "flows production._mail_the_recording (meeting.completed)"),
    Kind("report-daily", "Daily report", "report", OFF, "report-daily",
         {"date": "the day", "summary": "what happened, as the report step wrote it"},
         {"date": "Thu 16 Oct", "summary": "3 meetings, 5 commitments, 2 due today."}, "",
         link=True),
    Kind("report-weekly", "Weekly report", "report", OFF, "report-weekly",
         {"week": "the week", "summary": "what happened, as the report step wrote it"},
         {"week": "week 42", "summary": "11 meetings, 14 commitments, 3 overdue."}, "",
         link=True),
    Kind("digest", "Notification digest", "report", OFF, "digest",
         {"count": "how many notifications", "items": "one line per notification"},
         {"count": "3", "items": "- Anna shared Weekly product sync\n- Minutes ready: Kickoff\n"
                                 "- Bot joined: Board prep"}, "", link=True),
    Kind("reply", "Reply to a person's mail", "system", ON, None, {}, {},
         "flows production_agent (mail.reply) and the conversation drive"),
    Kind("rsvp", "Calendar acceptance (iMIP)", "system", ON, None, {}, {},
         "flows production.rsvp_accept (invite.received)"),
    Kind("quarantine-reply", "One line back to a stranger", "system", OFF, None, {}, {},
         "flows mailbox._fixed_reply (also needs VEXA_FLOWS_MAIL_QUARANTINE_REPLY)"),
)}


def get(kind: str) -> Kind:
    try:
        return KINDS[kind]
    except KeyError:
        raise KeyError(f"no mail kind {kind!r} — known: {', '.join(KINDS)}") from None


def editable(kind: str) -> bool:
    return get(kind).template is not None


def default_for(kind: str) -> str:
    """The kind's switch when the page says nothing: the shipped default, unless the operator set
    `VEXA_MAIL_KINDS_DEFAULT=on` (a deployment that wants every mail it sent before this catalogue
    existed to keep going until its admin account decides otherwise)."""
    import flows_config
    if (flows_config.get("VEXA_MAIL_KINDS_DEFAULT") or "").strip().lower() == ON:
        return ON
    return get(kind).default


def switch(rules: dict | None, kind: str, explicit: dict | None = None) -> str:
    """The kind's switch as the company holds it: the page's answer, else the default. A locked kind
    is always `on`, whatever the page says. `rules` is `policies.resolve`'s output, which carries
    `_explicit` — the keys the page actually wrote — so an unanswered row falls to `default_for`."""
    k = get(kind)
    if k.locked:
        return ON
    key = policy_key(kind)
    written = (rules or {}).get("_explicit")
    if written is not None and key not in written:
        return default_for(kind)
    value = str((rules or {}).get(key) or "").strip().lower()
    return value if value in SWITCH_VALUES else default_for(kind)


def policy_defaults() -> dict:
    """`mail.<kind>` → default, for `policies.DEFAULTS` (so the seed file and the code stay one set)."""
    return {policy_key(k.name): k.default for k in KINDS.values()}


def parse_caps(value) -> dict:
    """`mail_caps: minutes=50, digest=10, *=500` → {kind: per-day cap}. Unknown kinds and
    non-numbers are dropped; `*` is the cap for every kind without its own."""
    out: dict = {}
    for part in str(value or "").split(","):
        name, sep, n = part.partition("=")
        name, n = name.strip().lower(), n.strip()
        if sep and n.isdigit() and (name == "*" or name in KINDS):
            out[name] = int(n)
    return out
