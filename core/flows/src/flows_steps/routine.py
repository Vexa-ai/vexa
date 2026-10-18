"""A routine's agent turn, in the routine's own chat thread (ADR-0046 decisions 2, 7 and 12).

`agent_run` is the step a person's flow may name by default. It posts the flow's instruction as one
turn into a chat THREAD of the flow's owner — by default one thread per flow, `flow-<digest>`, so
every run appends to the same conversation and inherits what the earlier runs did and what the
person replied in between. agent-api runs that turn `autonomous` (trigger `scheduled`) on the
thread's sibling unit, marks it as the flow's, and names the thread after the flow in the rail.

ONE TURN AT A TIME PER THREAD. Before posting, the step asks agent-api whether the thread is busy —
something queued, or a turn running in either half of it — and if so it WAITS on flows' own durable
clock (a `Wait` burns no attempt) rather than posting a second turn beside the first. agent-api
checks again at the door and answers `409 thread_busy` to the race, which is also a `Wait` here.

The post is idempotent per run: the reaction id rides as the turn id, so a retry after a lost answer
withdraws the held copy before re-posting, and a confirmed post is never repeated (`scratch`)."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse

from flows import Done, StepCtx, StepError, Wait

from .agent import as_person
from .common import agent_door, http

#: How long a run waits before asking again whether its thread is free.
BUSY_WAIT_S = 30.0
THREAD_PREFIX = "flow-"


def thread_session(scope: str, name: str) -> str:
    """THE ROUTINE'S THREAD — stable per (scope, flow name), so every run of every version of the
    flow lands in the same conversation. Matches agent-api's chat session pattern."""
    digest = hashlib.sha1(f"{scope or 'instance'}|{name}".encode()).hexdigest()[:16]
    return f"{THREAD_PREFIX}{digest}"


#: A chat thread id `params.thread` may name: the chat the routine was created in.
CHAT_THREAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def run_session(ctx: StepCtx) -> str:
    """WHERE A RUN LANDS. By default (founder, 2026-10-13) the chat the routine was created in —
    `params.thread` names it — so each run continues that conversation. `new` gives a fresh thread
    per run; a routine made outside any chat (`flow` or no thread) gets its own `flow-<digest>`."""
    flow = ctx.flow
    base = thread_session(getattr(flow, "scope", "instance"), flow.name)
    thread = str(flow.param("thread", "flow") or "flow").strip()
    if thread.lower() == "new":
        return f"{base}-{ctx.reaction_id[:10]}"
    concern = concern_key(ctx)
    if concern:
        return concern_session(getattr(flow, "scope", "instance"), flow.name, concern)
    if thread.lower() != "flow" and CHAT_THREAD.match(thread):
        return thread
    return base


def delivery_rule(notify: str) -> str:
    """WHERE A ROUTINE REPORTS. Its own chat is where its person reads it, so that is the default;
    an outward channel only when the person named one (`params.notify`), and only that one."""
    notify = str(notify or "").strip()
    if not notify:
        return ("\n\n---\nWhere to report: your reply in this chat is how this routine tells its "
                "person — put any alert or result here. Do not send it anywhere else (no mail, "
                "message or connection) unless this instruction is changed to name one. If nothing "
                "changed since the last run, reply with one line starting `Checked — ` (for example "
                "`Checked — nothing new.`) and nothing else.")
    return ("\n\n---\nWhere to report: put the alert or result in this chat. You may also send it "
            f"outward through connection `{notify}` only — no other connection, and only when "
            "something matters. If that connection fails or is not available, say so here instead "
            "of trying another. If nothing changed since the last run, reply with one line "
            "starting `Checked — ` and send nothing.")


# ── event agents: one concern chat per occurrence ───────────────────────────────────────────────
#: `params.thread: "per:<ref>"` — an EVENT AGENT opens one chat per value of that ref of the event
#: (e.g. `per:meeting_id`: one follow-up chat per meeting) and routes a repeat into the same chat.
PER = "per:"


def is_event_agent(flow) -> bool:
    """A person's flow on an EVENT (not a schedule) is an event agent: autonomous and untrusted."""
    return bool(getattr(flow, "owner", "")) and not getattr(flow, "cron", None)


#: What an event agent is told it may do — and agent-api holds it to it (least privilege).
EVENT_AGENT_RULE = (
    "\n\n---\nYou are an event agent: you read and propose, nothing more. You cannot send or draft "
    "mail, write any workspace, share anything or change a connection — those are refused. For each "
    "such act the person should take, end your reply with a ```vexa fence holding one action: "
    '{"v":1,"blocks":[{"type":"action","label":"<what>","prompt":"<the exact ask>",'
    '"needs_go":true}]} — the person presses it, and it runs as their own turn.')


def concern_key(ctx: StepCtx) -> str:
    """`<ref>:<value>` for an event agent's occurrence, or "" (a routine, or no such ref)."""
    thread = str(ctx.flow.param("thread", "") or "").strip()
    if not thread.startswith(PER):
        return ""
    ref = thread[len(PER):].strip()
    value = str((ctx.refs or {}).get(ref) or "").strip()
    return f"{ref}:{value}" if ref and value else ""


def concern_session(scope: str, name: str, key: str) -> str:
    digest = hashlib.sha1(f"{scope or 'instance'}|{name}|{key}".encode()).hexdigest()[:16]
    return f"concern-{digest}"


def thread_state(uid: str, session: str) -> dict:
    """agent-api's answer about a thread: `busy`/`pending`, and `routine_cursor` — where the next
    routine turn will start on the thread's routine unit (how a run finds its own turn after)."""
    q = urllib.parse.urlencode({"session": session})
    code, out = http("GET", f"{agent_door()}/api/chat/pending?{q}", as_person(uid))
    if not 200 <= int(code or 0) < 300 or not isinstance(out, dict):
        raise StepError(f"agent-api could not say whether {session} is free: HTTP {code}")
    return out


def thread_busy(uid: str, session: str) -> bool:
    out = thread_state(uid, session)
    return bool(out.get("busy") or out.get("pending"))


def post_turn(uid: str, session: str, prompt: str, *, flow, turn_id: str,
              concern: str = "", grants: "list | None" = None) -> tuple[int, object]:
    headers = {**as_person(uid),
               **({"X-Vexa-Concern": concern} if concern else {}),
               # WHAT THIS RUN MAY DO UNATTENDED (flows phase 0): the routine's recorded grants, capped
               # by the policy as it reads now. agent-api mints them into the run's delegation.
               **({"X-Vexa-Flow-Grants": ",".join(grants)} if grants else {}),
               **({"X-Vexa-Flow-Kind": "event_agent"} if is_event_agent(flow) else {}),
               "X-Vexa-Flow": flow.ident if hasattr(flow, "ident") else flow.name,
               "X-Vexa-Flow-Step": "agent_run",
               "X-Vexa-Flow-Regime": "autonomous",
               "X-Vexa-Flow-Name": flow.name,
               "X-Vexa-Flow-Scope": getattr(flow, "scope", "instance"),
               "X-Vexa-Flow-Version": str(flow.version)}
    return http("POST", f"{agent_door()}/api/chat/submit", headers,
                {"prompt": prompt, "session": session, "turn_id": turn_id})


def register(reg, db=None) -> None:
    @reg.step(needs=("agent",), user_allowed=True)
    def agent_run(ctx: StepCtx):
        """Run the flow's owner's agent once, on `params.instruction`, as a turn in the routine's own
        chat thread (one thread per flow; `params.thread: new` for a fresh one each run). The turn
        runs with nobody in the loop — the `autonomous` regime — and waits while the thread is busy.
        It reports into that chat; `params.notify` names the one connection it may also send through.
        Result: {session, unit}.

        THE RUN ENDS AS ITS TURN ENDED (flows phase 0). Posting the turn is not the run: the step
        waits on flows' own clock for agent-api to say how the turn ended, and a turn whose acts
        were refused — or a one-shot that never did what it was granted to do — ends the run
        `failed` with the reason and one calm line in the chat, never `done`. The routine's grants
        (`params.grants`, capped by `_global/POLICIES.md`) ride the turn to agent-api."""
        posted = ctx.scratch.get("posted")
        if posted:
            return await_outcome(ctx, posted, db=db)
        flow = ctx.flow
        uid = str(getattr(flow, "owner", "") or ctx.refs.get("uid") or "").strip()
        if not uid:
            raise StepError("agent_run has nobody to run as: the flow has no owner and the event "
                            "names no uid", retryable=False)
        instruction = str(flow.param("instruction", "") or "").strip()
        if not instruction:
            raise StepError("agent_run needs params.instruction", retryable=False)
        session = run_session(ctx)
        state = thread_state(uid, session)
        if state.get("busy") or state.get("pending"):
            return Wait(seconds=BUSY_WAIT_S)
        prompt = instruction + (EVENT_AGENT_RULE if is_event_agent(flow)
                                else delivery_rule(flow.param("notify", "") or ""))
        from . import grants as _grants
        granted = [] if is_event_agent(flow) else _grants.effective(flow, uid)
        code, out = post_turn(uid, session, prompt, flow=flow, turn_id=ctx.reaction_id,
                              concern=concern_key(ctx), grants=granted)
        code = int(code or 0)
        if code == 409:
            return Wait(seconds=BUSY_WAIT_S)
        if not 200 <= code < 300:
            raise StepError(f"agent-api answered {code} to the routine's turn — {str(out)[:200]}",
                            retryable=code == 429 or code >= 500)
        posted = {"session": session, "unit": (out or {}).get("unit") if isinstance(out, dict)
                  else None, "uid": uid, "at": ctx.clock_now, "grants": granted,
                  "after": str(state.get("routine_cursor") or "")}
        ctx.scratch["posted"] = posted
        return Wait(seconds=OUTCOME_POLL_S)


# ── the run ends as its turn ended (flows phase 0) ───────────────────────────────────────────────
#: How often a run asks how its turn ended, and how long it waits before it says it does not know.
OUTCOME_POLL_S = 10.0
OUTCOME_DEADLINE_S = 3600.0

#: How a refused or missing act reads in the routine's chat, by the tool that should have done it.
VERBS = {"request_meeting_bot": "send the bot", "stop_bot": "stop the bot",
         "flow_lifecycle": "retire this routine"}


def tool_base(name: str) -> str:
    """`mcp__vexa__request_meeting_bot` → `request_meeting_bot`: harnesses prefix tool names."""
    return str(name or "").rsplit("__", 1)[-1].rsplit(".", 1)[-1]


def refusal_line(refused: dict) -> str:
    """One calm line for a refused act — what did not happen and why, in words."""
    tool = tool_base(refused.get("tool") or "")
    lead = f"Couldn't {VERBS[tool]}" if tool in VERBS else (
        f"Couldn't finish: `{tool}` was refused" if tool else "Couldn't finish")
    reason = str(refused.get("reason") or "refused")
    if reason == "human_session_required":
        return (f"{lead}: this routine isn't allowed to do that on its own. Ask in chat, or remake "
                "the routine and allow it.")
    return f"{lead} ({reason})."


def _turn_outcome(uid: str, session: str, turn_id: str, after: str = "") -> tuple[int, object]:
    q = urllib.parse.urlencode({"session": session, "turn_id": turn_id,
                                **({"after": after} if after else {})})
    return http("GET", f"{agent_door()}/api/chat/turn-outcome?{q}", as_person(uid))


def _notice_once(ctx: StepCtx, uid: str, session: str, text: str) -> None:
    if ctx.scratch.get("noticed"):
        return
    flow = ctx.flow
    try:
        post_notice(uid, session, text, name=flow.name, scope=getattr(flow, "scope", ""),
                    version=getattr(flow, "version", 0))
        ctx.scratch["noticed"] = True
    except Exception:  # noqa: BLE001 — the run's status says it whether or not the line lands
        pass


# ── a routine whose chat became shared pauses (Vexa-ai/vexa#1783) ──────────────────────────────
#: The refusal a turn other people read gets for a person-wide read (ADR-0048 decision 4).
SHARED_REASON = "shared_conversation"
#: `_global/POLICIES.md`: pause the routine on that refusal (default), or keep it running.
SHARED_POLICY_KEY = "routine_pauses_when_shared"

#: What a refused tool reads as, by a word in its name. The first match wins.
SOURCE_WORDS = (("gmail", "your mail"), ("mail", "your mail"), ("calendar", "your calendar"),
                ("transcript", "your meetings"), ("recording", "your recordings"),
                ("meeting", "your meetings"), ("connection", "your connections"),
                ("drive", "your files"))


def source_words(tool: str) -> str:
    """`mcp__vexa__gmail_search` → "your mail"; an unknown tool is named as itself."""
    base = tool_base(tool).lower()
    for word, said in SOURCE_WORDS:
        if word in base:
            return said
    return f"`{tool_base(tool)}`" if base else "a private source"


def _people(names) -> str:
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        return "other people"
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def shared_pause_text(shared_with, tool: str) -> str:
    """Why the routine is paused, as the strip shows it (after "Paused — ")."""
    return (f"this chat is now shared with {_people(shared_with)}, so {source_words(tool)} can't "
            "be read here. Unshare the chat, or move the routine to a private chat, then resume.")


def _pauses_when_shared(uid: str) -> bool:
    try:
        from . import policies
        value = policies.read(uid).get(SHARED_POLICY_KEY, True)
    except Exception:  # noqa: BLE001 — an unreadable policy is the default: pause
        return True
    return value is not False


def pause_flow(db, flow, text: str, now: float, reason: str = SHARED_REASON) -> bool:
    """Take this routine's version out of service (a pause is a retire: kept, and resumable) and
    record why on it, so its card says so. The owner's resume clears it. True when it paused."""
    if db is None or flow is None:
        return False
    from flows.db import loads
    scope, name, version = getattr(flow, "scope", "instance"), flow.name, int(flow.version)
    rows = db.execute("SELECT params FROM flow_version WHERE scope=:sc AND name=:n AND version=:v",
                      {"sc": scope, "n": name, "v": version})
    if not rows:
        return False
    params = loads(rows[0][0]) if rows[0][0] else {}
    params = {**(params or {}), "paused": {"reason": reason, "text": text, "at": float(now)}}
    db.execute("UPDATE flow_version SET status='retired', params=:p WHERE scope=:sc AND name=:n "
               "AND version=:v", {"p": json.dumps(params), "sc": scope, "n": name, "v": version})
    return True


def await_outcome(ctx: StepCtx, posted: dict, db=None):
    """Wait for the posted turn to end, then end the run the way it ended."""
    uid, session = str(posted.get("uid") or ""), str(posted.get("session") or "")
    try:
        code, out = _turn_outcome(uid, session, ctx.reaction_id, str(posted.get("after") or ""))
    except StepError:
        code, out = 0, None
    code = int(code or 0)
    if code in (404, 405):
        return Done(posted, provider_ref=session)          # an agent-api that cannot say: as before
    state = out.get("state") if 200 <= code < 300 and isinstance(out, dict) else ""
    if state != "done":
        if ctx.clock_now - float(posted.get("at") or ctx.clock_now) > OUTCOME_DEADLINE_S:
            _notice_once(ctx, uid, session, "Couldn't tell how this run ended: it did not finish "
                                            "within an hour.")
            raise StepError("no_outcome: the turn did not finish within the hour", retryable=False)
        return Wait(seconds=OUTCOME_POLL_S)
    refused = out.get("refused") if isinstance(out.get("refused"), dict) else None
    if (refused and refused.get("reason") == SHARED_REASON and ctx.flow is not None
            and _pauses_when_shared(uid)):
        # THE CHAT IS SHARED NOW, AND THIS RUN NEEDED SOMETHING ONLY ITS OWNER READS. Running it on
        # the owner's private scope and filtering what it says is not on offer (no filter for free
        # text is reliable), and running it again changes nothing — so it pauses, says why in one
        # line, and waits for its owner to unshare or move it, then resume.
        text = shared_pause_text(out.get("shared_with") or [], refused.get("tool") or "")
        if pause_flow(db, ctx.flow, text, ctx.clock_now):
            _notice_once(ctx, uid, session, f"Paused: {text}")
            raise StepError(f"refused: {SHARED_REASON} ({tool_base(refused.get('tool') or '')}) "
                            "— the routine is paused", retryable=False)
    if refused and refused.get("reason"):
        _notice_once(ctx, uid, session, refusal_line(refused))
        raise StepError(f"refused: {refused.get('reason')} ({tool_base(refused.get('tool') or '')})",
                        retryable=False)
    acted = {tool_base(t) for t in (out.get("acted") or [])}
    once = bool(ctx.flow.param("once", False)) if ctx.flow is not None else False
    if once and "bot.request" in (posted.get("grants") or []) and "request_meeting_bot" not in acted:
        _notice_once(ctx, uid, session, "Couldn't send the bot: this run ended without sending it.")
        raise StepError("no_action: the run never called request_meeting_bot", retryable=False)
    return Done({**posted, "outcome": {"ok": bool(out.get("ok", True)), "acted": sorted(acted)}},
                provider_ref=session)


# ── sinks: a chat subscribed to an event stream ───────────────────────────────────────────────
#: A sink's step, and the per-chat cap on event cards an hour (cards never count as runs).
SINK_STEP = "post_event_card"
CARDS_PER_HOUR = 60


def event_card(event_type: str, refs: dict, when: float) -> str:
    """One compact line for an event: source, what, time, link — plain text, no agent."""
    import time as _t
    what = ", ".join(f"{k} {str(v)[:60]}" for k, v in sorted((refs or {}).items())
                     if not k.startswith("_") and k not in ("uid", "url", "link", "web_url") and v not in (None, ""))[:200]
    link = next((str(refs[k]) for k in ("web_url", "url", "link") if (refs or {}).get(k)), "")
    return (f"⇢ {event_type} · {what or 'new event'} · {_t.strftime('%H:%M UTC', _t.gmtime(when))}"
            + (f" · {link}" if link else ""))


def matches(filter_: dict, refs: dict) -> bool:
    """A sink's filter: every `{ref: text}` pair must appear (case-insensitive) in that ref."""
    for k, want in (filter_ or {}).items():
        if str(want).lower() not in str((refs or {}).get(k, "")).lower():
            return False
    return True


def wake_due(policy: str, card: str, last_wake: "float | None", now: float) -> bool:
    """never · per_event · batch:<minutes> · match:<text> — does this card wake the chat's agent?"""
    p = str(policy or "never").strip()
    if p == "per_event":
        return True
    if p.startswith("batch:"):
        try:
            gap = max(1.0, float(p.split(":", 1)[1])) * 60
        except ValueError:
            return False
        return last_wake is None or now - last_wake >= gap
    if p.startswith("match:"):
        return p.split(":", 1)[1].strip().lower() in card.lower()
    return False


def register_sinks(reg, db=None) -> None:
    @reg.step(needs=("agent",), user_allowed=True)
    def post_event_card(ctx: StepCtx):
        """A SINK: post the event as one compact card into the subscribed chat (`params.thread`) —
        no agent run, never counted as a run — when it matches `params.filter`; wake the chat's agent
        only as `params.wake` says (never | per_event | batch:<min> | match:<text>), and only within
        the person's daily run limit. Cards are capped per chat per hour. Result: {posted, woke}."""
        if ctx.scratch.get("done"):
            return Done(ctx.scratch["done"])
        flow = ctx.flow
        uid = str(getattr(flow, "owner", "") or "").strip()
        thread = str(flow.param("thread", "") or "").strip()
        if not uid or not CHAT_THREAD.match(thread or "-"):
            raise StepError("a sink needs its owner and params.thread (the chat it feeds)", retryable=False)
        refs = dict(ctx.refs or {})
        if not matches(flow.param("filter", {}) or {}, refs):
            ctx.scratch["done"] = {"posted": False, "reason": "filtered"}
            return Done(ctx.scratch["done"])
        now = ctx.clock_now
        card = event_card(ctx.reaction.event_type, refs, now)
        cap = int(flow.param("cards_per_hour", CARDS_PER_HOUR) or CARDS_PER_HOUR)
        if _cards_last_hour(db, ctx, now) >= cap:
            ctx.scratch["done"] = {"posted": False, "reason": "rate_cap"}
            return Done(ctx.scratch["done"])
        code, _ = post_notice(uid, thread, card, name=flow.name, scope=getattr(flow, "scope", ""),
                              version=getattr(flow, "version", 0), kind="sink")
        if not 200 <= int(code or 0) < 300:
            raise StepError(f"agent-api answered {code} to an event card", retryable=int(code or 0) >= 500)
        woke = False
        if wake_due(flow.param("wake", "never"), card, _last_wake(db, ctx), now) and _may_wake(db, uid, now):
            if not thread_busy(uid, thread):
                instruction = str(flow.param("instruction", "") or "Look at the new event card above.")
                c2, _ = post_turn(uid, thread, instruction + delivery_rule(""), flow=flow,
                                  turn_id=ctx.reaction_id)
                woke = 200 <= int(c2 or 0) < 300
        ctx.scratch["done"] = {"posted": True, "woke": woke, "thread": thread}
        return Done(ctx.scratch["done"], provider_ref=thread)


def _sink_rows(db, ctx, since: float):
    from flows.db import loads
    if db is None:
        return []
    return [(at, loads(sc) if sc else {}) for at, sc in db.execute(
        "SELECT created_at, scratch FROM reaction WHERE flow = :f AND created_at > :t "
        "AND reaction_id <> :r", {"f": ctx.flow.ident, "t": since, "r": ctx.reaction_id})]


def _cards_last_hour(db, ctx, now: float) -> int:
    return sum(1 for _, sc in _sink_rows(db, ctx, now - 3600)
               if ((sc or {}).get("done") or {}).get("posted"))


def _last_wake(db, ctx) -> "float | None":
    woke = [at for at, sc in _sink_rows(db, ctx, 0) if ((sc or {}).get("done") or {}).get("woke")]
    return max(woke) if woke else None


def _may_wake(db, uid: str, now: float) -> bool:
    if db is None:
        return True
    from flows import admission
    cap = getattr(admission, "RUNS_PER_DAY", 48)
    try:
        import flows_config
        cap = flows_config.get_int("VEXA_FLOWS_USER_RUNS_PER_DAY")
    except Exception:  # noqa: BLE001
        pass
    return admission.runs_today(db, uid, now, scheduled=False) < cap


# ── agents speak to each other (ADR-0046 decision 13) ───────────────────────────────────────────

#: The carrier `message_agent` publishes, and the instance flow that delivers it.
MESSAGE_EVENT = "thread.message"
MESSAGE_FLOW = "agent_messages"


def post_message(refs: dict, *, turn_id: str) -> tuple[int, object]:
    """Queue the message as a turn in the target thread, marked as the sending agent's. It runs with
    nobody in the loop — nobody typed it — and its origin chain rides along, so the receiving
    agent's reply carries the conversation's hop count forward."""
    uid = str(refs.get("uid") or "")
    headers = {**as_person(uid),
               "X-Vexa-Flow": MESSAGE_FLOW, "X-Vexa-Flow-Step": "deliver_message",
               "X-Vexa-Flow-Regime": "autonomous",
               "X-Vexa-Message-From-Uid": str(refs.get("sender_uid") or ""),
               "X-Vexa-Message-From-Thread": str(refs.get("sender_thread") or ""),
               "X-Vexa-Message-From-Label": str(refs.get("sender_label") or "")[:120],
               "X-Vexa-Message-Id": str(refs.get("message_id") or ""),
               "X-Vexa-Message-Origin": json.dumps(list(refs.get("_origin") or []))}
    return http("POST", f"{agent_door()}/api/chat/submit", headers,
                {"prompt": str(refs.get("text") or ""), "session": str(refs.get("thread") or ""),
                 "turn_id": turn_id})


def register_messages(reg) -> None:
    @reg.step(needs=("agent",))
    def deliver_message(ctx: StepCtx):
        """Deliver one agent's message into another agent's chat thread as a queued turn, marked as
        from the sending agent (its thread, and its person or routine) and never as the person's
        words; waits while the thread is busy. Result: {thread, uid}."""
        if ctx.scratch.get("delivered"):
            return Done(ctx.scratch["delivered"])
        uid, thread = str(ctx.refs.get("uid") or ""), str(ctx.refs.get("thread") or "")
        if not uid or not thread or not str(ctx.refs.get("text") or "").strip():
            raise StepError("thread.message carried no uid, thread or text", retryable=False)
        if thread_busy(uid, thread):
            return Wait(seconds=BUSY_WAIT_S)
        code, out = post_message(ctx.refs, turn_id=ctx.reaction_id)
        code = int(code or 0)
        if code == 409:
            return Wait(seconds=BUSY_WAIT_S)
        if not 200 <= code < 300:
            raise StepError(f"agent-api answered {code} to a delivered message — {str(out)[:200]}",
                            retryable=code == 429 or code >= 500)
        delivered = {"thread": thread, "uid": uid}
        ctx.scratch["delivered"] = delivered
        return Done(delivered, provider_ref=thread)


# ── a refused run says so where the person reads it ────────────────────────────────────────────

def post_notice(uid: str, session: str, text: str, *, name: str, scope: str,
                version: int = 0, kind: str = "") -> tuple[int, object]:
    """One line in the routine's chat — a note, never an agent turn (agent-api's `notice` step) —
    which also marks that chat as the routine's."""
    headers = {**as_person(uid), "X-Vexa-Flow": f"{scope}/{name}", "X-Vexa-Flow-Step": "notice",
               "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow-Name": name,
               "X-Vexa-Flow-Scope": scope, "X-Vexa-Flow-Version": str(int(version or 0)),
               **({"X-Vexa-Flow-Kind": kind} if kind else {})}
    return http("POST", f"{agent_door()}/api/chat/submit", headers,
                {"prompt": text, "session": session})


def announce_refusals(db, *, limit: int = 20) -> int:
    """Post each recorded schedule refusal (`flows.schedule.record_refusal`, once per flow, reason
    and day) into its routine's chat, once. Returns how many were posted."""
    from flows.db import loads
    rows = db.execute("SELECT reaction_id, subject_refs, flow, scratch FROM reaction "
                      "WHERE event_type = 'schedule.refused' ORDER BY created_at LIMIT :n",
                      {"n": int(limit) * 5})
    posted = 0
    for rid, refs_raw, ident, scratch_raw in rows:
        scratch = loads(scratch_raw) if scratch_raw else {}
        if (scratch or {}).get("noticed") or posted >= limit:
            continue
        refs = loads(refs_raw) if refs_raw else {}
        uid, scope, name = str(refs.get("uid") or ""), str(refs.get("scope") or ""), str(refs.get("flow") or "")
        if not uid or not name:
            continue
        prow = db.execute("SELECT params FROM flow_version WHERE scope=:sc AND name=:n "
                          "ORDER BY version DESC LIMIT 1", {"sc": scope, "n": name})
        params = loads(prow[0][0]) if prow and prow[0][0] else {}
        thread = str((params or {}).get("thread") or "").strip()
        session = thread if thread and thread.lower() not in ("flow", "new") and CHAT_THREAD.match(
            thread) else thread_session(scope, name)
        code, _ = post_notice(uid, session, str(refs.get("text") or ""), name=name, scope=scope)
        if 200 <= int(code or 0) < 300:
            db.execute("UPDATE reaction SET scratch = :s WHERE reaction_id = :r",
                       {"s": json.dumps({**(scratch or {}), "noticed": True}), "r": rid})
            posted += 1
    return posted


STAGES = ("opened", "active", "waiting", "resolved", "archived")


def post_stage(uid: str, session: str, stage: str, note: str = "") -> tuple[int, object]:
    """Move a concern chat to `stage` — a line in the chat, and the stage the chat and the home show."""
    headers = {**as_person(uid), "X-Vexa-Flow": "concern", "X-Vexa-Flow-Step": "stage",
               "X-Vexa-Flow-Regime": "autonomous", "X-Vexa-Flow-Name": "concern",
               "X-Vexa-Flow-Scope": f"user:{uid}", "X-Vexa-Flow-Version": "0",
               "X-Vexa-Concern-Stage": stage}
    return http("POST", f"{agent_door()}/api/chat/submit", headers,
                {"prompt": note or f"Stage: {stage}.", "session": session})
