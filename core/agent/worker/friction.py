"""friction.py (worker) — THE AGENT SIDE OF THE ROUGH-EDGES LOOP (PRD decision 33 §1).

Three things, and the third is the one that matters:

  1. **The rule, in every turn's context.** `friction_preamble()` — "when something did not work as
     expected, call `report_friction` with what you tried, what happened and the ids; a silent
     workaround is the defect."
  2. **A client.** `report()` posts one record to agent-api, best-effort, never raising into a turn.
  3. **THE HARNESS FILES WITHOUT THE MODEL'S HELP.** A turn that ends on a tool error, an MCP server
     that was not attached at spawn, a 4xx/5xx from a vexa tool — each files a record whether or not
     the model noticed, remembered the rule, or was still able to make a call at all.

The third exists because the first two cannot be relied on, and we have the measurement: on
2026-09-02 a whole session ran with no MCP tools attached (ledger F70) and reported nothing —
because reporting requires a tool and there were none. **A reporting channel that only works while
the product works is not a reporting channel.** Every rule that asks a model to remember something
mid-failure is a rule with a failure rate; the auto-filed record has none, and its cost is a
duplicate row that the dedup key folds into the model's own report anyway.

WHAT IT DOES NOT DO: it does not judge. A tool error that the agent recovered from is still filed —
the whole point of §1's "a silent workaround is the defect" is that the workaround is invisible from
the outside, and a heuristic that files only unrecovered errors reproduces the blindness it exists
to cure. Deduplication makes the noise cheap; silence is what is expensive.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from shared.git_redaction import redact
from pathlib import Path

log = logging.getLogger(__name__)

#: Where the report goes. `VEXA_AGENT_API_SELF_URL` is an already-declared agent-api config key
#: (config.v1.json, class `defaulted`, no deploy target) — the worker reads the same name the
#: control plane does rather than inventing a second one for the same address.
TIMEOUT_S = 3.0

#: THE FILE FIRST, ALWAYS — the rule the rig's original `report_friction` was written around, kept
#: verbatim: "losing feedback because a store was briefly down is the worst failure available to the
#: one channel that tells us what using this is like." A worker container is ephemeral, so this is
#: a weaker fallback here than on the rig host; it is still the difference between a lost report and
#: a report somebody can find in `docker logs`.
FALLBACK_LOG = Path(os.environ.get("TMPDIR", "/tmp")) / "vexa-friction.jsonl"


def _api() -> str:
    return (os.environ.get("VEXA_AGENT_API_SELF_URL") or "http://agent-api:8100").rstrip("/")


def fallback_session() -> str:
    """The best session id available OUTSIDE a turn -- at spawn, before a chat session exists at
    all (#1510). `spawn_gap` is filed by `mcp_delegation_config`, which runs before the harness and
    has no `session` parameter of its own to thread; `VEXA_CHAT_SESSION` is only ever set in this
    container's env for a MESSAGE-triggered dispatch (`dispatch.build_unit_env`), so a scheduled,
    event or transcription dispatch has none. `VEXA_UNIT_ID` is set on EVERY dispatch and is stable
    for the life of this container, so it is the honest fallback rather than a made-up string: the
    flows carrier this record is published onto (`control_plane.publish.publish_friction`, via
    agent-api's forward) refuses a report with no session at all, so silently filing one with
    `session=""` is not an option any more."""
    return (os.environ.get("VEXA_CHAT_SESSION") or os.environ.get("VEXA_UNIT_ID")
           or "unknown").strip() or "unknown"


# ── the rule that rides every turn ───────────────────────────────────────────────────────────────

def friction_preamble() -> str:
    """READ SILENTLY. Ships on EVERY turn, for the same reason `voice_preamble` does.

    It was NOT put in the composed-opening machinery note, and that is deliberate — the note reaches
    only turns a link composed, and the founder has already watched one rule fail exactly that way
    (see `voice_preamble`'s own warning: a `+` chat is not a composed opening and never saw it). An
    edge is hit in whatever turn hits it.

    The wording carries decision 33 §1's four demands and nothing else: what you tried, what
    happened, the ids, and that routing around it silently is itself the defect."""
    return (
        "## When something does not work\n\n"
        "Call `report_friction` — a missing tool, a refusal you could not act on, a page that did "
        "not exist, a link that landed in the wrong workspace, a request you could not fulfil, an "
        "error, or a step that took five calls when it should have taken one. Say what you TRIED, "
        "what HAPPENED, and the ids you had (session · workspace · page · meeting · tool · the "
        "error text). Half-formed is fine.\n\n"
        "**A silent workaround is the defect.** Routing around a rough edge and saying nothing is "
        "the only outcome that guarantees it is still there tomorrow — you are the only one who "
        "knows what you expected. Report it and carry on: it does not interrupt the work, and it "
        "never goes to the person you are talking to.\n\n"
    )


# ── the client ───────────────────────────────────────────────────────────────────────────────────

def report(record: dict, *, subject: str = "", timeout: float = TIMEOUT_S) -> dict | None:
    """File one record. Returns the stored record, or None — and NEVER raises.

    A failure to report friction must not become a failure of the turn: the caller is a `finally`
    block around somebody's actual work. The failure is logged and the record is appended to the
    fallback file, so it is recoverable from the container's own log even when agent-api is the
    thing that is broken — which, given what this channel reports on, is a case that will happen."""
    # SCRUBBED BEFORE IT IS DURABLE (#1416's rule, applied to this writer too). `shared/friction.py`
    # redacts every free-text field on the way in — `_clip` — but that is the SERVER's copy of the
    # path. This function writes two other places: the fallback log below, which is a file in the
    # container that outlives the turn, and the request body on the wire. A record reaches here
    # carrying whatever the turn was about, and the F70 detector deliberately puts the person's
    # prompt and the agent's reply into one — so a pasted token would land in both. Shape-based, so
    # it does not depend on anyone having recognised the value as a secret first.
    body = {k: (redact(v) if isinstance(v, str) else v) for k, v in dict(record).items()}
    body.setdefault("reporter", "agent")
    if subject and not body.get("subject"):
        body["subject"] = str(subject)
    try:
        FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FALLBACK_LOG.open("a") as f:
            f.write(json.dumps({"at": time.time(), **body}) + "\n")
    except OSError as e:
        log.warning("friction: could not write the fallback log (%s)", e)
    req = urllib.request.Request(
        f"{_api()}/api/friction", method="POST", data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 **({"x-user-id": str(subject)} if subject else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        log.warning("friction: could not file %r (%s) — it is in %s",
                    (body.get("happened") or "")[:80], e, FALLBACK_LOG)
        return None


# ── what the harness files by itself ─────────────────────────────────────────────────────────────

#: An HTTP status in a tool's own error text. The rig returns its failures as prose carrying the
#: code (`bot_say returned 404 {'detail': 'Not Found'}`), so this is how a 4xx/5xx from a vexa tool
#: is recognised without the tool having to cooperate.
_STATUS = re.compile(r"\b([45]\d{2})\b")

#: Which tool names belong to the product's own surface. A `Read` that failed is the agent reading a
#: file that is not there; a `mcp__vexa__*` that failed is OUR TOOL failing, which is a different
#: report with a different reader.
VEXA_TOOL = re.compile(r"^mcp__vexa__", re.I)


def _kind_for(tool: str, summary: str) -> str:
    if VEXA_TOOL.search(tool or "") and _STATUS.search(summary or ""):
        return "error"
    if re.search(r"no such file|does not exist|not found", summary or "", re.I):
        return "no-page"
    return "error"


# THE REFUSAL SHAPE. Narrow on purpose: it matches a claim about the SESSION'S CAPABILITY, not
# any sentence with "cannot" in it. "I cannot reach the API right now" is a report of a failure and
# must pass; "I don't have a bot-dispatch tool in this session" is a claim about the tool list, and
# the tool list is a fact the process can check.
_DISBELIEF = re.compile(
    r"(?:don'?t|do not|can'?t|cannot)\s+(?:have|trigger|dispatch|access|see)"
    r"[^.]{0,60}?(?:tool|from here|in this session|this session)", re.I)

# VERB -> the tool that does it. Small and closed: a map that guesses would re-run turns on a
# coincidence, and a re-run is not free. Ordered longest-phrase-first is unnecessary — every key is
# a whole word and the request is matched word-wise.
_VERB_TOOL: tuple[tuple[tuple[str, ...], str], ...] = (
    (("send", "drop", "put", "join", "admit", "dispatch"), "bot_send"),
    (("schedule", "book"), "bot_schedule"),
    (("stop", "remove", "pull"), "bot_stop"),
    (("transcript", "transcribe", "read along"), "meeting_transcript"),
    (("say", "speak"), "bot_say"),
)


def disbelieved_capability(prompt: str, reply: str, tools) -> "str | None":
    """The tool this turn REFUSED while holding it, or None (F70).

    On 2026-09-02 the founder asked for a bot and was told "I don't have a bot-dispatch tool in this
    session". `bot_send` was in the list; the CLI logged `hasTools: true`; the model never attempted
    a call. Asked afterwards to enumerate its tools it listed them all and said it had been "guessing
    at my own capabilities instead of checking them".

    Three conditions, all required, because each alone is common and harmless:
      1. the reply claims a missing capability (`_DISBELIEF`),
      2. the request names a verb we have a tool for,
      3. THAT TOOL IS IN THE SESSION'S LIST.
    Condition 3 is the one that makes this safe to act on: with the tool absent the refusal is true
    and the turn was right."""
    if not prompt or not reply or not tools:
        return None
    if not _DISBELIEF.search(reply):
        return None
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    have = {str(t).rsplit("__", 1)[-1] for t in tools}
    for verbs, tool in _VERB_TOOL:
        if tool in have and words.intersection(verbs):
            return tool
    return None


def budget_stop(trunc: dict, *, last_tool: str = "", session: str = "", subject: str = "",
                workspace: str = "") -> dict:
    """The record for a turn that ENDED ON ITS BUDGET (Vexa-ai/vexa#1622).

    Four of these were auto-filed from the founder's own chats on 2026-09-06 and every one of them
    was **malformed in the same two ways**::

        tried:    the turn ended on a failed tool call: ``
        happened: not run: the turn hit its tool-call budget

    The empty name is not a rendering slip. A turn that runs out of budget refuses the calls it has
    not made yet, and a refused call emits no ``tool-call`` event — so the generic scan below, which
    joins a result to its call by id, found nothing and printed an empty pair of backticks. And
    *"not run"* is the message the MODEL is given about ONE skipped call; as a description of what
    happened to the turn it is simply false — the turn ran, it ran out.

    So a budget stop gets its own record, saying the three things a fixing agent needs: the budget,
    the count against it, and the last tool the turn was actually on.

    Severity is `annoyance` rather than `blocker` DELIBERATELY, and only because of what shipped
    beside it: the same issue makes the stop visible in the bubble and offers a Continue act, so
    the work is one press from carrying on. Filed against a turn that still stopped silently this
    would be a blocker, and it was."""
    reason = str(trunc.get("reason") or "budget")
    calls = int(trunc.get("calls") or 0)
    budget = int(trunc.get("budget") or 0)
    kind = str(trunc.get("kind") or "").strip()
    tool = str(trunc.get("tool") or last_tool or "").strip()
    # NEVER AN EMPTY NAME. A turn can genuinely spend its budget before running anything (a budget
    # of 0, a first assistant message with more calls in it than the budget allows), and saying so
    # in words is the honest answer — an empty pair of backticks is not.
    named = f"`{tool}`" if tool else "none — the budget was spent before any tool ran"
    return {
        "reporter": "agent", "subject": subject, "session": session, "kind": "unfulfilled",
        "tried": f"finish this turn inside its {reason}"
                 + (f" ({budget} tool calls for a {kind} turn)" if budget and kind
                    else f" ({budget} tool calls)" if budget else ""),
        "happened": f"budget exhausted at {calls} of {budget} calls, last tool {named}. The turn "
                    "stopped mid-work and answered with whatever it had.",
        "would_help": "a budget that fits this kind of turn "
                      f"(VEXA_AGENT_MAX_TOOL_CALLS_{(kind or 'chat').upper()}), or the work split "
                      "so it does not need one turn this long",
        "severity": "annoyance",
        "context": {"tool": tool, "error": f"{reason}: {calls}/{budget} calls",
                    "workspace": workspace},
        "auto": True,
    }


def scan_turn(events: list[dict], *, session: str = "", subject: str = "",
              workspace: str = "") -> list[dict]:
    """The records a finished turn's event stream earned, if any.

    Three triggers, per decision 33 §2 and the ledger:

      * **the turn ENDED on a tool error** — the last `tool-result` in the stream came back
        `ok=False`. The turn stopping there is what makes it worth a record: whatever the model said
        afterwards, its last action did not work.
      * **any vexa tool answered 4xx/5xx** — anywhere in the turn, recovered or not. This is OUR
        surface failing and it is filed on sight (F71's neighbour: an agent that works around our
        own 404 and tells nobody is the case this loop exists for).
      * **the turn RAN OUT OF BUDGET** (Vexa-ai/vexa#1622) — a `turn-truncated` event. It REPLACES
        the first trigger rather than joining it: the failed result at the end of such a turn is the
        refusal the budget caused, so filing both would file one event twice, once accurately and
        once as ``the turn ended on a failed tool call: ``.

    Pure: it reads events and returns records. Filing is the caller's, so a test can assert the
    decision without a network."""
    calls: dict[str, dict] = {}
    results: list[tuple[str, dict]] = []
    trunc: dict = {}
    last_tool = ""
    for ev in events or []:
        t = ev.get("type")
        if t == "tool-call":
            calls[str(ev.get("callId") or "")] = ev
            last_tool = str(ev.get("tool") or "") or last_tool
        elif t == "tool-result":
            results.append((str(ev.get("callId") or ""), ev))
        elif t == "turn-truncated":
            trunc = ev
    if not results and not trunc:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def add(call: dict, res: dict, why: str) -> None:
        # THE RESULT'S OWN NAME IS BELIEVED FIRST (Vexa-ai/vexa#1622). A result whose call never ran
        # carries no `tool-call` event to join to, and `call` is then `{}` — which is how four
        # reports came to name no tool at all. The harness stamps `tool` on such a result now, so
        # this reads the record in front of it before falling back to the join.
        tool = str(res.get("tool") or call.get("tool") or "")
        cid = str(res.get("callId") or f"{tool}:{len(out)}")
        if cid in seen:
            return
        seen.add(cid)
        summary = str(res.get("summary") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        out.append({
            "reporter": "agent", "subject": subject, "session": session,
            "kind": _kind_for(tool, summary),
            "tried": f"{why}: `{tool}`" + (f" with {json.dumps(args)[:200]}" if args else ""),
            "happened": summary[:900] or "the call failed with no message",
            "severity": "annoyance",
            "context": {"tool": tool, "error": summary[:600],
                        "workspace": str(args.get("workspace") or workspace or ""),
                        "path": str(args.get("path") or ""),
                        "meeting_id": str(args.get("meeting_id") or args.get("meeting") or "")},
            "auto": True,
        })

    # a vexa tool that answered 4xx/5xx, anywhere in the turn
    for cid, res in results:
        if res.get("ok"):
            continue
        call = calls.get(cid, {})
        if VEXA_TOOL.search(str(call.get("tool") or "")) and _STATUS.search(str(res.get("summary") or "")):
            add(call, res, "a vexa tool answered an HTTP error")
    # the turn ran out of budget — the accurate record, INSTEAD of the tail one below
    if trunc:
        out.append(budget_stop(trunc, last_tool=last_tool, session=session, subject=subject,
                               workspace=workspace))
        return out
    # the turn ended on a tool error
    last_cid, last = results[-1]
    if not last.get("ok"):
        add(calls.get(last_cid, {}), last, "the turn ended on a failed tool call")
    return out


def spawn_gap(*, url: str, token: str, config_written: bool, session: str = "",
              subject: str = "") -> dict | None:
    """The record for an MCP server that was NOT attached at spawn — ledger F70, or None.

    F70 in one line: a session ran with no vexa tools at all, told the founder to `curl` a public
    API, and filed nothing, *because filing requires a tool*. So this is checked by the code that
    builds the attachment, before the model exists to notice, and it is deliberately narrow — it
    reports only the case the spawn can PROVE:

      * the dispatch intended a toolbelt (a url, or a token, was handed over) and
      * the attachment was not written.

    **What it cannot see, stated rather than implied:** the F70 session's config file *was* written
    and the server *was* reachable AT SPAWN — a restart AFTER that, before some later turn ran, is
    invisible from here by construction (this only runs once, at container boot). That guard is
    `mcp_preflight` in `worker.engine`, run per-turn rather than once at spawn, and the record it
    files on failure is `mcp_unreachable` below — see F153."""
    intended = bool((url or "").strip() or (token or "").strip())
    if not intended or config_written:
        return None
    missing = [n for n, v in (("VEXA_MCP_URL", url), ("VEXA_MCP_DELEGATION_TOKEN", token))
               if not (v or "").strip()]
    detail = (f"half-configured spawn: {', '.join(missing)} absent" if missing
              else "no writable directory for the delegation config")
    return {
        "reporter": "agent", "subject": subject, "session": session, "kind": "missing-tool",
        "tried": "attach the vexa MCP toolbelt to this worker at spawn",
        "happened": f"the toolbelt was NOT attached — {detail}. This turn runs with no vexa tools; "
                    "anything it says about meetings, workspaces or bots is unsourced.",
        "would_help": "a spawn that fails loudly when the toolbelt it was told to attach cannot be "
                      "attached, rather than a turn that runs as a text-only agent",
        "severity": "blocker",
        "context": {"tool": "mcp:vexa", "error": detail},
        "auto": True,
    }


def mcp_unreachable(*, url: str, detail: str, attempts: int, session: str = "",
                     subject: str = "") -> dict:
    """The record for an MCP server that WAS attached at spawn and did NOT answer at TURN start —
    ledger F153, the gap `spawn_gap` names above and cannot see from the spawn side.

    The control server is stateless by design and restarts routinely; each turn is already a fresh
    harness subprocess that re-reads the attachment and re-connects from scratch, so a restart
    between turns should be invisible. It was not, on 2026-09-03: the harness attached to a server
    mid-restart, got nothing back, and the turn ran silently with no vexa tools — the model then
    told the founder its own guess about why, instead of the truth. `worker.engine.mcp_preflight`
    runs the same handshake with retries BEFORE the turn, so this is filed the moment the retry
    budget (~15s) is spent, not discovered mid-turn by a tool call that never had anywhere to go."""
    return {
        "reporter": "agent", "subject": subject, "session": session, "kind": "missing-tool",
        "tried": f"reach the vexa MCP server at {url or '(no url)'} ({attempts} attempts, ~15s) "
                 "before running this turn",
        "happened": f"the server never answered — {detail}. This turn runs WITHOUT the vexa "
                    "toolbelt; anything it says about meetings, workspaces or bots is unsourced.",
        "would_help": "the control server should not go down mid-session, or should come back "
                      "inside one turn's retry window",
        "severity": "blocker",
        "context": {"tool": f"mcp:{url or 'vexa'}", "error": detail},
        "auto": True,
    }
