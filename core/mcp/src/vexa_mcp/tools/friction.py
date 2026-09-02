"""FRICTION — the loop that turns a rough edge into a fix (PRD decision 33). Forwards to agent-api.

ONE STORE, ONE OWNER. ``core/agent/shared/friction.py`` defines the record, the dedup and the
renderer; agent-api holds it. The rig also wrote and read a second ``friction`` table on the flows
Postgres over a direct ``psycopg.connect`` with a URL out of a dotfile (seam inventory B6.3) — a
second store for one fact, reachable only from one host, deleted by the lane's own blank script.
That path is gone: a failed read here says it failed rather than quietly answering from a different
table.
"""
from __future__ import annotations

import json
import urllib.parse

from .. import config
from ..config import AGENT_API
from ..httpc import http as _http
from ..identity import anon_guard, me, subject
from ..shaping import capped
from ..registry import tool


def friction_post(path: str, body: dict, uid: str = ""):
    """POST to agent-api's friction surface. Returns ``(status, body)`` exactly like ``_http``.

    WHY AGENT-API: the people half of decision 33 ("Report this" in the terminal) posts there,
    agent-api cannot reach the flows lane, the lane's blank script deletes that table with the rest
    of the lane, and it has no columns for the context, log pointers, status or fix reference this
    record needs. The reasoning is written down where the record is defined —
    ``core/agent/shared/friction.py``."""
    return _http("POST", f"{AGENT_API}{path}", {"X-User-Id": uid} if uid else {}, body)


@tool
def report_friction(what_i_was_doing: str, what_went_wrong: str,
                    what_would_have_helped: str = "", tool: str = "",
                    severity: str = "annoyance",
                    kind: str = "", workspace: str = "", path: str = "",
                    meeting_id: str = "", scaffold_id: str = "", error: str = "") -> str:
    """Tell us what did not work. NO ACCOUNT NEEDED. Use this freely and often.

    You are the only one who can close this loop. We can see that a call failed; we cannot see
    what your person asked for, what you expected, or what you tried instead — and that is the
    part that would fix it. A rough edge you route around silently is one we never learn about.

    Report anything: a tool that did the wrong thing, a description that misled you, a step you
    expected to exist, a refusal you could not act on, documentation that contradicted the
    behaviour, or a workflow that took five calls when it should have taken one. Half-formed is
    fine — 'I could not tell whether X had worked' is a real report.

    THE IDS ARE THE HALF THAT MAKES IT FIXABLE. Pass whatever you had — `tool`, `workspace`,
    `path`, `meeting_id`, `scaffold_id`, and the verbatim `error` text. A report without them is
    still worth filing; a report with them can be reproduced without asking you.

    kind: missing-tool | refusal | no-page | wrong-workspace | unfulfilled | error | ux | other
    (omit it and it is inferred from what you wrote).
    severity: blocker | annoyance | papercut | idea

    Nothing you send is published. It goes to a ledger a human reads."""
    import time as _t
    uid = subject() or ""
    rec = {
        "at": _t.time(),
        "reporter": "agent",
        "subject": uid,
        # NO SESSION ID. The rig is stateless by contract (tests/test_rig_stateless.py: *"nothing
        # depends on the transport session"*), so the MCP transport's session id is not a fact
        # about anything — it is empty or meaningless after a restart. The record's `session` is
        # the CHAT session, which this server does not know; the worker fills it in, and an empty
        # string here is the honest answer rather than an id that reads as one.
        "session": "",
        "kind": kind or "",
        "tried": (what_i_was_doing or "")[:900],
        "happened": (what_went_wrong or "")[:900],
        "would_help": (what_would_have_helped or "")[:900],
        "severity": severity if severity in ("blocker", "annoyance", "papercut", "idea")
                    else "annoyance",
        "context": {k: v for k, v in (("tool", tool), ("workspace", workspace), ("path", path),
                                      ("meeting_id", meeting_id), ("scaffold_id", scaffold_id),
                                      ("error", error or what_went_wrong)) if v},
    }
    # THE FILE FIRST, ALWAYS. It is the fallback, not the store: if the database is
    # unreachable the report still lands somewhere, and losing feedback because a store was
    # briefly down is the worst failure available to the one channel that tells us what using
    # this is like.
    try:
        with config.FRICTION_LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        ok = True
    except Exception:  # noqa: BLE001
        ok = False

    # then the durable store, deduped SERVER-SIDE: the same edge reported twice is one row
    # carrying the newest wording and a count, not two rows nobody can total.
    st, body = friction_post("/api/friction", rec, uid)
    known, out = False, {}
    if 200 <= st < 300 and isinstance(body, dict):
        ok, known, out = True, bool(body.get("known")), body
    return json.dumps({
        "recorded": ok,
        "id": out.get("id", ""),
        "already_known": known,
        "occurrences": out.get("recurrence", 1),
        "thank_you": "This is the only signal we get about what it is actually like to use "
                     "this. Keep going — do not let it interrupt what you were doing.",
    })


@tool
@anon_guard
def friction_so_far(token: str = "") -> str:
    """Everything reported through report_friction, newest first. NO ACCOUNT NEEDED.

    Useful before reporting: if the thing you hit is already here, add what is different about
    your case rather than filing it again."""
    uid = me()   # account-scoped: this touches shared state
    st, body = _http("GET", f"{AGENT_API}/api/friction/dump?status=&format=json",
                     {"X-User-Id": uid})
    if 200 <= st < 300 and isinstance(body, dict):
        return capped({"count": body.get("count", 0), "reports": body.get("records", [])[:40]},
                      12000)
    # NO SECOND STORE. The rig fell back to `psycopg.connect` on the flows Postgres, reading a
    # `friction` table past its owner with a URL out of `~/.storm/dburl` (seam inventory B6.3) —
    # and the legacy rows it read predate the store moving to agent-api. One owner, one answer, and
    # a failed read says so rather than quietly returning a different table.
    return json.dumps({"error": "the reports could not be read", "status": st,
                       "detail": str(body)[:300],
                       "do": "say so plainly — do not invent a list; report_friction() if it repeats"})


@tool
@anon_guard
def friction_dump(since: str = "", status: str = "open", token: str = "") -> str:
    """THE FIXER'S BRIEF: every open rough edge, grouped by likely cause, ready to work.

    This is decision 33 §3 — the thing the whole loop exists to produce. It returns MARKDOWN, in
    the alpha ledger's finding shape (symptom · exact context · likely cause · log pointers ·
    repro), deduplicated with occurrence counts, `recurring` first. Hand it to a fixing agent
    verbatim; it needs no other briefing.

    since: "" (everything) · "2h" · "3d" · an ISO instant.
    status: "open" (the default — includes `recurring`, which is the most urgent work there is) ·
            "fixed" · "recurring" · "" for all.

    When you fix something from it, close it: `friction_fixed([ids], "<commit or PR>")`."""
    uid = me()
    q = f"?since={urllib.parse.quote(since)}&status={urllib.parse.quote(status)}&format=md"
    st, body = _http("GET", f"{AGENT_API}/api/friction/dump{q}", {"X-User-Id": uid})
    if not (200 <= st < 300):
        return json.dumps({"error": f"agent-api answered {st}", "detail": str(body)[:300],
                           "do": "the dump is unreadable — say so plainly; do not invent one"})
    return str(body)[:60000]


@tool
@anon_guard
def friction_fixed(ids: list[str], fix_ref: str, token: str = "") -> str:
    """Close the rough edges a change addressed (decision 33 §4).

    `fix_ref` is whatever lets the next reader find the change — a commit sha, a PR url, a branch,
    or one sentence. Closing is CHEAP and meant to be: a record filed again after a fix flips itself
    to `recurring`, so a fix that did not hold announces itself instead of hiding. Close what you
    addressed; do not close what you merely looked at."""
    uid = me()
    if not str(fix_ref or "").strip():
        return json.dumps({"error": "fix_ref is required",
                           "why": "a record marked fixed with nothing to point at is "
                                  "indistinguishable from one somebody wanted off the list"})
    out = []
    for rid in list(ids or [])[:100]:
        st, body = friction_post(f"/api/friction/{urllib.parse.quote(str(rid))}/fix",
                                  {"fix_ref": fix_ref}, uid)
        out.append({"id": rid, "ok": 200 <= st < 300,
                    "status": (body or {}).get("status") if isinstance(body, dict) else str(body)[:120]})
    return json.dumps({"closed": sum(1 for r in out if r["ok"]), "results": out})
