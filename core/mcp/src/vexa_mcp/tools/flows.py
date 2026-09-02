"""FLOWS — facts in, reactions out, and the queue a person's agent works. Forwards to flows-api.

``whats_waiting`` is the exception that proves the rule: it forwards to AGENT-API, because the queue
is assembled there now. It was 253 lines and 40% of all measured traffic — four service fan-outs, a
keyword list deciding whether a failure was ours or theirs, the entire cold-start welcome script and
two menus, all as Python string literals inside a tool (seam inventory B2). Every sentence of the
product's first thirty seconds was baked into an image nobody could edit without a deploy.

The two mail verbs read the mail DOUBLE, which is not a service — it is the dev lane's inbox, and it
is where the outbound half of a flow can be read exactly as a person receives it.
"""
from __future__ import annotations

import json
import urllib.parse

from .. import config
from ..config import AGENT_API, FLOWS_API
from ..httpc import flows_headers as _fkey, http as _http
from ..identity import CALL_TOKEN, GHOST_HINT, GHOST_UID, NotOperator, anon_guard, me, operator_or_refuse, operator_refusal, subject
from ..shaping import capped, refuse_if_gated
from ..registry import tool


@tool
@anon_guard
def flows_list(token: str = "") -> str:
    """Every flow version the engine knows plus the full step vocabulary with contracts.

    Read this before writing a flow: `steps` must be names from `steps_vocabulary`, and a
    name that is not in it is rejected at submission with a 400 rather than failing at run
    time.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    me()   # account-scoped: this touches shared state
    st, body = _http("GET", f"{FLOWS_API}/flows", _fkey())
    return capped({"status": st, **(body if isinstance(body, dict) else {"body": body})}, 12000)


@tool
@anon_guard
def flows_submit(name: str, on_event: str, steps: list[str],
                 params: dict | None = None, activate: bool = True,
                 token: str = "") -> str:
    """Submit a flow as DATA and (by default) activate it. Live in about ten seconds — the
    worker hot-reloads active rows; no image rebuild, no deploy.

    steps: ordered step names from flows_list's vocabulary.
    on_event: a trigger name, e.g. invite.received / meeting.completed / mail.reply.
    params: flow-level tuning read by steps via ctx.flow.param(key).

    REFUSED while the company layer is missing: a flow submitted into an instance that cannot yet
    say who it works for is a machine configured for nobody."""
    try:
        _actor = operator_or_refuse("flows_submit")
    except NotOperator as e:
        return json.dumps({"refused": "operator only", "verb": e.verb, "who": e.who,
                           "why": e.why,
                           "what_to_do": "An instance admin can run this. A harness or other "
                                         "non-person producer should use flows-api POST /events "
                                         "or /events/batch with the lane's admin key."})
    gated = refuse_if_gated("flows_submit", me())
    if gated:
        return gated
    st, body = _http("POST", f"{FLOWS_API}/flows", _fkey(), {
        "name": name, "on_event": on_event, "steps": steps,
        "params": params or {}, "activate": activate})
    return capped({"status": st, "result": body}, 4000)


@tool
@anon_guard
def flow_lifecycle(name: str, version: int, verb: str, token: str = "") -> str:
    """Activate or retire one flow version. verb: activate | retire.

    In-flight reactions keep the version stamped at their admission — retiring never
    rewrites work already running.

    REFUSED while the company layer is missing, for the same reason flows_submit is."""
    try:
        _actor = operator_or_refuse("flow_lifecycle")
    except NotOperator as e:
        return json.dumps({"refused": "operator only", "verb": e.verb, "who": e.who,
                           "why": e.why,
                           "what_to_do": "An instance admin can run this. A harness or other "
                                         "non-person producer should use flows-api POST /events "
                                         "or /events/batch with the lane's admin key."})
    gated = refuse_if_gated("flow_lifecycle", me())
    if gated:
        return gated
    if verb not in ("activate", "retire"):
        return json.dumps({"error": "verb must be activate or retire"})
    st, body = _http("POST", f"{FLOWS_API}/flows/{name}/{version}/{verb}", _fkey(), {})
    return capped({"status": st, "result": body}, 3000)


@tool
@anon_guard
def reactions_list(status: str = "", token: str = "") -> str:
    """The operator projection: what happened, why, and what is waiting.

    status filters to one of admitted/running/blocked/retrying/failed/cancelled/done.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    me()   # account-scoped: this touches shared state
    q = f"?status={status}" if status else ""
    st, body = _http("GET", f"{FLOWS_API}/reactions{q}", _fkey())
    return capped({"status": st, "result": body}, 12000)


@tool
@anon_guard
def reaction_signal(reaction_id: str, verb: str, token: str = "") -> str:
    """Steer one reaction. Every signal is an audited row, never shell surgery on the table.

    resume — answer a blocked step (the human is the effect); only on 'blocked'
    retry  — replay a failure as a new attempt; only on 'failed'
    cancel — stop it; on admitted/retrying/blocked/running
    wake   — re-check NOW something that is deliberately sleeping between polls; on
             retrying/admitted. Use this when you have just satisfied the condition a
             step was waiting on and do not want to wait out its poll interval."""
    me()   # account-scoped: this touches shared state
    st, body = _http("POST", f"{FLOWS_API}/reactions/{reaction_id}/{verb}", _fkey(), {})
    return capped({"status": st, "result": body}, 3000)


@tool
@anon_guard
def timeline(since: str = "", until: str = "", limit: int = 20, token: str = "") -> str:
    """WHAT HAS HAPPENED TO YOU AND WHAT IS COMING — your own events, in order (PRD decision 31).

    Invites that arrived, meetings scheduled and held, reports delivered, mail sent, replies
    handled, and anything that failed — merged from the flows engine's own facts and receipts with
    your meetings table, scoped to you as organizer or attendee. Every time is in YOUR zone, and
    `now` is stated first so a relative answer ("this morning", "in an hour") has something to be
    relative to.

    since / until: epoch seconds or ISO-8601. Empty means 14 days back and 30 days forward, so the
    answer covers both halves of the question — what just happened, and what is next.

    Read-only. It never sends, schedules or cancels anything.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    uid = me()
    q = urllib.parse.urlencode({k: v for k, v in
                                {"subject": uid, "since": since, "until": until,
                                 "limit": max(1, min(int(limit or 20), 200)),
                                 "format": "text"}.items() if v != ""})
    st, body = _http("GET", f"{FLOWS_API}/timeline?{q}", _fkey())
    if st != 200 or not isinstance(body, dict):
        return json.dumps({"error": "the timeline is not available", "status": st,
                           "detail": str(body)[:300],
                           "note": "the flows route answers this; every other tool is unaffected"})
    # A THIN FORWARD, on purpose (PRD §3.3). The zone lookup and the rendering happen in the owning
    # service, where the person's `.settings.json` is already read — not here, and not a second
    # time in the dispatch preamble, which asks the same route for `format=preamble`. One renderer
    # is why a chat and a machinery note cannot disagree about when a meeting was.
    text = body.get("text")
    if isinstance(text, str) and text.strip():
        return text[:12000]
    return capped({"status": st, "result": body}, 12000)


@tool
@anon_guard
def fact_emit(event_type: str, source_event_id: str, subject_refs: dict,
              token: str = "") -> str:
    """Inject a fact and let every matching flow admit its own reaction.

    This is the system's real front door — the mailbox poller is just one producer of
    facts. Admission dedups on (source_event_id, flow), so re-emitting the same id is a
    no-op rather than a duplicate.

    invite.received wants: organizer, url, start (epoch), ics_uid, title, group|null."""
    try:
        _actor = operator_or_refuse("fact_emit")
    except NotOperator as e:
        return operator_refusal(e)
    # THE INTAKE, NOT THE ENGINE. The rig injected the flows engine into its own process —
    # `sys.path.insert` on a source tree named by `VEXA_FLOWS_SRC`, a Postgres URL read out of
    # `~/.storm/dburl`, `Registry()`, `production.build()`, `admit()` — so the MCP was not a client
    # of flows here, it WAS flows, briefly (seam inventory B6.4). flows-api has taken facts over
    # `POST /events` since it existed; that route runs the same admission inside the service that
    # owns the store, against the vocabulary that service has hydrated.
    st, body = _http("POST", f"{FLOWS_API}/events", _fkey(),
                     {"event_type": event_type, "source_event_id": source_event_id,
                      "subjectrefs": subjectrefs})
    if not (200 <= st < 300):
        return json.dumps({"error": "the fact could not be filed", "status": st,
                           "detail": str(body)[:300],
                           "note": "the flows intake answers this; every other tool is unaffected"})
    out = body if isinstance(body, dict) else {"result": body}
    return json.dumps({"admitted": out.get("admitted", out.get("reactions", 0)),
                       "event_type": event_type, **{k: v for k, v in out.items()
                                                    if k not in ("admitted", "event_type")}})


@tool
def whats_waiting(token: str = "") -> str:
    """START HERE on every connection — EXCEPT the one case named below, which is common.
    Everything Vexa needs from this person, in one read.

    Vexa cannot reach your agent when you are not connected — there is no live session after a
    meeting ends at night. So work waits here and you pull it. Call this first, work what it
    returns, then call it again until it is empty.

    THE EXCEPTION, and it is the common one: if this turn's message opens with a BRACKETED TAG —
    ANY [...] at all, not a fixed list; every _global/asks/* preset starts with one and new presets
    appear without this text changing — your person clicked a link about ONE meeting and that
    opening is their question. Answer it FIRST, then call this. A queue is not an answer to "what
    should I know before this meeting", and leading with one reads as changing the subject. A
    preset phrase that sounds like the queue ("what they missed", "what they owe someone") is
    scoped to the meeting the tag names and is answered from the workspace, not from here.

    Returns four kinds of item:
      setup      — the workspace is not scaffolded yet; Vexa cannot write minutes until it is
      question   — a claim Vexa needs confirmed before treating it as company context
      blocked    — a reaction stopped on a human gate; answer it with reaction_signal(resume)
      stuck      — a reaction failing with a reason worth a human eye
    """
    CALL_TOKEN.set(token or None)
    uid = subject()
    if not uid:
        # A GHOST IS NOT A NEWCOMER. This is the first call every agent makes, so the greeting it
        # returns is the product's first sentence — and returning the welcome to somebody whose
        # account was deleted tells them to set Vexa up again when what they actually need is to
        # bind the account they already have. The two failures look identical from here (no uid)
        # and have opposite fixes, which is exactly why the resolution point records which.
        ghost = GHOST_UID.get()
        if ghost:
            return json.dumps({**GHOST_HINT, "uid": ghost, "tool": "whats_waiting",
                               "authenticated": False, "waiting": 0, "items": []})
        st, body = _http("GET", f"{AGENT_API}/api/queue/waiting?anonymous=1", {})
        if 200 <= st < 300 and isinstance(body, dict):
            return capped(body, 12000)
        return json.dumps({"error": "could not read the queue", "status": st,
                           "authenticated": False,
                           "do": "say the READ failed — never that nothing is waiting"})
    # THE POLICY ENGINE MOVED. This was 253 lines and 40% of all measured traffic: four service
    # fan-outs, a keyword list deciding whether a failure was ours or theirs, the entire cold-start
    # welcome script and two `next_options` menus — every sentence of the product's first thirty
    # seconds as a Python string literal inside a tool (seam inventory B2). agent-api assembles it
    # now, from the stores it owns, and this is the forward.
    st, body = _http("GET", f"{AGENT_API}/api/queue/waiting", {"X-User-Id": uid})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not read the queue", "status": st,
                           "detail": str(body)[:300],
                           "tell_your_person": "Say the READ failed — never that nothing is "
                                               "waiting. You do not know that.",
                           "do": "report_friction() with this"})
    return capped(body, 12000)


@tool
@anon_guard
def mail_inbox(limit: int = 20, token: str = "") -> str:
    """Read the mail double. Every message the system has sent, with nothing leaving the
    host — this is the outbound half of the loop and the honest way to check what a flow
    actually said to a person. Account-scoped: an open inbox would let an agent read the
    sign-in codes and skip the human."""
    me()
    st, body = _http("GET", f"{config.MAILPIT}/api/v1/messages?limit={limit}", None)
    if isinstance(body, dict):
        msgs = [{"from": m["From"]["Address"],
                 "to": [t["Address"] for t in m.get("To", [])],
                 "subject": m["Subject"], "id": m["ID"]}
                for m in body.get("messages", [])]
        return capped({"total": body.get("total"), "messages": msgs}, 8000)
    return json.dumps({"status": st, "body": str(body)[:400]})


@tool
@anon_guard
def mail_read(message_id: str, token: str = "") -> str:
    """The full body of one sent message — the artifact as the person receives it."""
    me()
    st, body = _http("GET", f"{config.MAILPIT}/api/v1/message/{message_id}", None)
    if isinstance(body, dict):
        return json.dumps({"subject": body.get("Subject"),
                           "text": (body.get("Text") or "")[:6000]})
    return json.dumps({"status": st, "body": str(body)[:400]})
