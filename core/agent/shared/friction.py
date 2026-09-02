"""friction.py — THE ROUGH-EDGES RECORD (PRD decision 33).

Founder, 2026-09-02 16:0xZ: *"we also need to leverage the mcp tool that should collect rough edges
— things that did not work as expected — and dump it in a way that we can just dump that to an
agent that would just fix that (like you are here)."*

This module is the RECORD and nothing else: the shape, the normalization that lets three very
different reporters write the same row, the dedup key, the status machine, and the renderer that
turns a pile of rows into a brief a fixing agent can work straight off. No storage, no HTTP, no
clock beyond the one it is handed. The store is `control_plane/friction.py`; the agent-side client
is `worker/friction.py`; the person-side control is the terminal's `ReportThis.tsx`. All four speak
this shape, and this file is the only place it is written down.

── WHY THE STORE MOVED OFF THE FLOWS DATABASE ───────────────────────────────────────────────────
`report_friction` already existed on the rig and wrote a `friction` table in the FLOWS Postgres.
That table is the wrong owner, for four reasons, and the fourth is the one that decides it:

  1. **The people half cannot reach it.** Decision 33 §2 puts a "Report this" action in the
     terminal, which posts to agent-api. The rig reaches the flows lane by reading
     `~/.storm/dburl` off the HOST filesystem; agent-api is a container and has no such file and no
     flows credential. One store with two owners is the shape that produces two stores.
  2. **The blank script deletes it.** `deploy/dogfood/bin/blank-instance.sh` lists `friction` among
     the flows lane tables it wipes. Friction is a ledger of what is BROKEN IN THE PRODUCT — it must
     outlive the lane whose reset it is describing. Wiping the evidence with the environment is how
     the same defect gets found four times.
  3. **It exists in one lane and not the other** ("absent in this lane — skipped"), because nothing
     ever created it in a migration. A table with no DDL in the repository is not a store, it is a
     thing somebody typed once.
  4. **It cannot hold this record.** Decision 33 needs a context object, log pointers, a status, a
     fix reference and a recurrence counter. The existing five columns hold none of them, and adding
     them means writing the migration the flows lane never had — for a table that is about to be
     read by a service that cannot connect to it anyway.

So the store is agent-api's redis, beside the scaffold store and the session index, for the reasons
`control_plane/scaffolds.py` already states about scaffolds and which apply verbatim here: it is
durable by deployment (valkey with `--appendonly yes` on its own volume), it is not the workspace
volume, and an in-memory fallback keeps the unit tests store-free. The legacy flows rows are not
migrated and not deleted; `friction_so_far` still reads them as a last fallback so nothing already
filed disappears.

── THE FILE IS STILL WRITTEN FIRST ──────────────────────────────────────────────────────────────
The rig's original code had one rule worth keeping verbatim: the append-only JSONL lands BEFORE the
durable store, because "losing feedback because a store was briefly down is the worst failure
available to the one channel that tells us what using this is like." That stays. The store moving
does not change which write is the fallback.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

from shared.git_redaction import redact

# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────

#: Who filed it. Two values, and the difference is load-bearing in the dump: an agent's report is
#: written by the thing that hit the edge, a person's is written by the thing that SAW it, and a
#: fixing agent reads them differently.
REPORTERS = ("agent", "person")

#: What kind of edge it is (decision 33 §1's list, closed). `other` is the honest floor — a reporter
#: who cannot classify must still be able to file, and a record forced into a wrong bucket is worse
#: than one in the bucket named "I could not tell".
KINDS = ("missing-tool", "refusal", "no-page", "wrong-workspace", "unfulfilled", "error", "ux",
         "other")

#: The status machine. `open` on file; `fixed` when a fixing agent references it; `recurring` when
#: the same edge is filed AGAIN after a fix — which is the single most valuable state here, because
#: it is the only one that says a fix did not hold.
STATUSES = ("open", "fixed", "recurring")

#: The context object's keys, in the order the dump prints them. `surface` carries the decision-30
#: human-surface block when the client has one; it is optional by design — decision 30's server half
#: is not on this branch yet, and a record that refuses to exist without it would file nothing today.
CONTEXT_KEYS = ("workspace", "path", "meeting_id", "scaffold_id", "tool", "error", "surface")

#: Severity is not in decision 33's shape. It is kept because the rig's `report_friction` has always
#: taken it, dozens of live rows carry it, and dropping an argument is not backwards compatibility.
SEVERITIES = ("blocker", "annoyance", "papercut", "idea")

MAX_TEXT = 900          # per free-text field; a report is a lead, not a transcript
MAX_ERROR = 600


def _clip(v, n: int = MAX_TEXT) -> str:
    """Trim ONE free-text field — and scrub it. Every string a person or an agent writes into a
    friction report passes through here, and a friction record is DURABLE BY DESIGN (this file's own
    docstring: "nothing here expires"). A credential pasted into "what went wrong" would therefore
    outlive the session, the fix and the rotation. Shape-based, so it does not depend on anybody
    having recognised the value as a secret first — which on 2026-09-02 nobody did."""
    return redact(str(v or "").strip())[:n]


def _one_of(v, allowed, default: str) -> str:
    v = str(v or "").strip().lower()
    return v if v in allowed else default


# ── normalization ────────────────────────────────────────────────────────────────────────────────

# Today's rig arguments → the record's fields. `report_friction(what_i_was_doing=…,
# what_went_wrong=…, what_would_have_helped=…)` has been live long enough to have callers in the
# wild (the machinery note names it by that signature), so the old spelling is not deprecated, it is
# an ALIAS. A caller that passes both wins with the new one.
_ALIASES = {
    "what_i_was_doing": "tried",
    "doing": "tried",
    "what_went_wrong": "happened",
    "wrong": "happened",
    "what_would_have_helped": "would_help",
}


def normalize(raw: dict, *, now: float | None = None) -> dict:
    """One record, in the shape of decision 33 §1 — from whatever the reporter managed to say.

    Accepts the legacy rig arguments, the new named fields, a flat context (``tool=``, ``path=`` at
    the top level, which is what a person's one-line report produces) or a nested one. Everything
    unknown is DROPPED rather than carried: a context key nobody reads is a key the dump has to
    decide how to print, and this record is read by an agent that will act on it.

    It never raises. A report that fails validation is a report that never gets filed, and the whole
    point of this channel is that filing is cheaper than routing around the problem silently."""
    raw = raw if isinstance(raw, dict) else {}
    src = dict(raw)
    for old, new in _ALIASES.items():
        if src.get(old) and not src.get(new):
            src[new] = src[old]

    ctx_in = src.get("context")
    ctx_in = dict(ctx_in) if isinstance(ctx_in, dict) else {}
    ctx: dict = {}
    for k in CONTEXT_KEYS:
        # top level wins only when the nested object is silent — a caller that sent a real context
        # object meant it, and a stray top-level `path` should not overwrite it.
        v = ctx_in.get(k) if ctx_in.get(k) not in (None, "") else src.get(k)
        if v in (None, ""):
            continue
        if k == "surface":
            ctx[k] = v if isinstance(v, dict) else {"note": _clip(v, 400)}
        elif k == "error":
            ctx[k] = _clip(v, MAX_ERROR)
        else:
            ctx[k] = _clip(v, 200)

    rec = {
        "at": float(src.get("at") or (now if now is not None else time.time())),
        "reporter": _one_of(src.get("reporter"), REPORTERS, "agent"),
        "subject": _clip(src.get("subject") or src.get("uid"), 64),
        "session": _clip(src.get("session"), 128),
        "kind": _one_of(src.get("kind"), KINDS, _infer_kind(src, ctx)),
        "tried": _clip(src.get("tried")),
        "happened": _clip(src.get("happened")),
        "would_help": _clip(src.get("would_help")),
        "severity": _one_of(src.get("severity"), SEVERITIES, "annoyance"),
        "context": ctx,
        "log_refs": log_refs(src.get("log_refs"), ctx, src.get("reporter")),
    }
    return rec


# The words a reporter who did not classify actually uses. This is a GUESS and is treated as one:
# it only ever fires when `kind` was absent, and `other` is where an unrecognised report lands. It
# exists because the alternative — every unclassified report in one bucket — makes the dump's
# grouping useless on exactly the reports written in a hurry, which are most of them.
_KIND_HINTS = (
    ("missing-tool", re.compile(r"no (?:such )?tool|tool (?:is )?(?:missing|absent|unavailable)|"
                                r"don'?t have a .{0,24}tool|no mcp|mcp server (?:absent|missing)|"
                                r"tools unavailable", re.I)),
    ("no-page", re.compile(r"\b(?:page|file|doc(?:ument)?) (?:does not|doesn'?t) exist|"
                           r"no page (?:here|at)|404|not found", re.I)),
    ("wrong-workspace", re.compile(r"wrong workspace|wrong desk|other (?:workspace|desk)|"
                                   r"landed in the wrong", re.I)),
    ("refusal", re.compile(r"\brefus|\bdenied\b|not permitted|forbidden|403", re.I)),
    ("error", re.compile(r"\b(?:4\d\d|5\d\d)\b|traceback|exception|failed with", re.I)),
    ("unfulfilled", re.compile(r"could not (?:do|fulfil|fulfill|complete)|no way to|"
                               r"gave up|worked around", re.I)),
)


def _infer_kind(src: dict, ctx: dict) -> str:
    hay = " ".join(str(src.get(k) or "") for k in ("happened", "tried", "would_help"))
    hay = f"{hay} {ctx.get('error', '')}"
    for kind, rx in _KIND_HINTS:
        if rx.search(hay):
            return kind
    return "other"


# ── the dedup key ────────────────────────────────────────────────────────────────────────────────

# Volatile ids inside an error string are what stop dedup from working at all: the same failure
# carries a different meeting row, uuid or timestamp every time, so ten reports of one defect look
# like ten defects. They are masked BEFORE hashing — the key answers "is this the same edge?", and
# a row id is not part of the answer.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
# An id is recognised by WHAT IT IS CALLED, not by how long it is. Masking every 3-digit run was
# the obvious rule and it is wrong in the direction that matters: it eats HTTP status codes, so
# `404` and `503` from one tool collapse into a single "edge" and a fixing agent is told two
# different failures are the same one. So a number is masked when a word beside it says it is an
# identifier, and otherwise only when it is too long to be a status.
_LABELLED_ID = re.compile(
    r"\b(meeting|meetings|uid|user|users|row|id|ids|session|scaffold|workspace|desk|chat|"
    r"reaction|flow|thread)\s*[#:=]?\s*\d+", re.I)
_NUM = re.compile(r"\b\d{4,}\b")
_WS = re.compile(r"\s+")

#: How much of the error text takes part in the key. Long enough to separate two different failures
#: of one tool, short enough that a stack trace's tail does not make every occurrence unique.
ERROR_PREFIX = 120


def _mask(s: str) -> str:
    s = _UUID.sub("<id>", str(s or ""))
    s = _HEX.sub("<id>", s)
    s = _LABELLED_ID.sub(lambda m: f"{m.group(1)} <id>", s)
    s = _NUM.sub("<n>", s)
    return _WS.sub(" ", s).strip().lower()


def error_prefix(rec: dict) -> str:
    """The masked head of what went wrong — the third leg of the key.

    `context.error` when the reporter gave one (a machine-readable failure), otherwise `happened`
    (what a person or an agent wrote in prose). One field would be wrong in both directions: an
    error-only key cannot dedup a human report, and a prose-only key cannot dedup a 500."""
    ctx = rec.get("context") or {}
    return _mask(ctx.get("error") or rec.get("happened") or "")[:ERROR_PREFIX]


def dedup_key(rec: dict) -> str:
    """`(kind, tool|path, error prefix)` — decision 33 §1's key, hashed.

    `tool` before `path` because a tool failure is about the tool wherever it was pointed, while a
    page failure has no tool and is entirely about the path. A record with neither keys on the error
    alone, which is right: that is all it told us."""
    ctx = rec.get("context") or {}
    subject = _mask(ctx.get("tool") or ctx.get("path") or "")
    payload = f"{rec.get('kind', 'other')}|{subject}|{error_prefix(rec)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def group_key(rec: dict) -> tuple[str, str]:
    """The DUMP's grouping — "same kind + tool/path" (decision 33 §3), one step coarser than dedup.

    Two different 500s from one tool are two rows and one finding: a fixing agent opens that tool's
    code once. Grouping at the dedup key instead would hand them the same file twice."""
    ctx = rec.get("context") or {}
    return (rec.get("kind", "other"), _mask(ctx.get("tool") or ctx.get("path") or ""))


# ── log pointers ─────────────────────────────────────────────────────────────────────────────────

# WHERE TO LOOK, per kind of report. This is the difference between a dump an agent can act on and
# a dump it has to investigate from scratch — and it is derived, never asked for: a reporter who
# knew which container held the answer would not be reporting friction.
#
# It is a HINT and the dump says so. A wrong container costs one `docker logs` call; a missing one
# costs the fixing agent the whole investigation.
AGENT_API = "vexa-dogfood-agent-api-1"
RUNTIME = "vexa-dogfood-runtime-1"
TERMINAL = "vexa-dogfood-terminal-1"
GATEWAY = "vexa-dogfood-gateway-1"

_CONTAINERS = {
    "missing-tool": (AGENT_API,),        # dispatch mints the delegation config the toolbelt rides on
    "refusal": (AGENT_API,),
    "no-page": (AGENT_API,),
    "wrong-workspace": (AGENT_API,),
    "unfulfilled": (AGENT_API, RUNTIME),
    "error": (AGENT_API, RUNTIME),
    "ux": (TERMINAL,),
    "other": (AGENT_API,),
}
# A tool name that says more than the kind does. `bot_*` and `meeting_*` cross the gateway; anything
# `flow*` is the flows runtime.
_TOOL_CONTAINERS = (
    (re.compile(r"^(?:mcp__vexa__)?(?:bot_|meeting_|recordings_|transcript)", re.I), GATEWAY),
    (re.compile(r"^(?:mcp__vexa__)?(?:flow|propose|whats_waiting)", re.I), RUNTIME),
    (re.compile(r"^(?:mcp__vexa__)?workspace|^(?:mcp__vexa__)?entity_", re.I), AGENT_API),
)

#: How far before the report to start reading. A worker turn that ends in an error wrote the cause
#: minutes earlier, not seconds.
LOOKBACK_S = 900


def _grep_for(ctx: dict, rec: dict) -> str:
    """What to grep the container's log FOR. The most specific identifier the record carries, and
    only as a fallback the first few words of the symptom — a grep for a whole sentence matches
    nothing, because a log line is not a paragraph."""
    for k in ("tool", "path", "meeting_id", "scaffold_id"):
        if ctx.get(k):
            return str(ctx[k])[:80]
    words = _mask(rec.get("happened") or "").split(" ")[:4]
    return " ".join(words)[:80]


def log_refs(given, ctx: dict | None = None, reporter=None) -> list[dict]:
    """`[{container, since, grep}]` — supplied by the reporter, or nothing.

    A reporter that names its own pointers is believed and they are kept as-is: a worker knows its
    own container id and this function does not. Everything else is left EMPTY here and derived at
    render time by `derived_log_refs`, where the record's kind and time are both known and where a
    renamed deployment does not make a stored pointer a lie."""
    if isinstance(given, list) and given:
        out = []
        for r in given[:6]:
            if isinstance(r, dict) and r.get("container"):
                out.append({"container": _clip(r["container"], 120),
                            "since": _clip(r.get("since"), 40),
                            "grep": _clip(r.get("grep"), 120)})
        if out:
            return out
    return []


def derived_log_refs(rec: dict) -> list[dict]:
    """The pointers for a record that carries none — computed from its kind, tool and time.

    Not stored: a stored pointer goes stale the moment the deployment is renamed, and this is
    cheap to recompute. `since` is an absolute RFC-3339 instant, which is what `docker logs
    --since` wants and what survives being pasted into a terminal an hour later."""
    if rec.get("log_refs"):
        return list(rec["log_refs"])
    ctx = rec.get("context") or {}
    since = datetime.fromtimestamp(max(0.0, float(rec.get("at") or 0) - LOOKBACK_S),
                                   tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    containers: list[str] = []
    tool = str(ctx.get("tool") or "")
    for rx, c in _TOOL_CONTAINERS:
        if tool and rx.search(tool):
            containers.append(c)
            break
    for c in _CONTAINERS.get(rec.get("kind", "other"), (AGENT_API,)):
        if c not in containers:
            containers.append(c)
    grep = _grep_for(ctx, rec)
    return [{"container": c, "since": since, "grep": grep} for c in containers[:3]]


def log_command(ref: dict) -> str:
    """One pasteable line. `grep -F` because a tool name is a literal, not a pattern, and an
    `entity_upsert(` in a grep expression is a syntax error the reader has to debug instead of the
    defect they came for."""
    cmd = f"docker logs --since {ref.get('since') or '1h'} {ref.get('container')}"
    g = ref.get("grep")
    return f"{cmd} 2>&1 | grep -F {g!r}" if g else cmd


def repro_line(rec: dict) -> str:
    """One line a fixing agent can run or replay. Honest about what it does not know: where no
    reproducible call exists, it names the surface and the ask instead of inventing a command."""
    ctx = rec.get("context") or {}
    tool = ctx.get("tool")
    # A `subsystem:name` tool is not something you can call — `mcp:vexa` is the toolbelt itself, not
    # a function. Printing `mcp:vexa()` as a repro is the dump inventing a command, which is exactly
    # the confident-wrong answer a fixing agent will waste a pass on.
    if tool and ":" in str(tool):
        return (f"start a worker turn with the `{tool}` attachment in the state the context "
                f"describes, as subject {rec.get('subject') or '?'} — this is a SPAWN condition, "
                "not a call")
    if tool:
        args = []
        if ctx.get("workspace"):
            args.append(f"workspace={ctx['workspace']!r}")
        if ctx.get("path"):
            args.append(f"path={ctx['path']!r}")
        if ctx.get("meeting_id"):
            args.append(f"meeting={ctx['meeting_id']!r}")
        return f"call `{tool}({', '.join(args)})` as subject {rec.get('subject') or '?'}"
    if ctx.get("path"):
        ws = ctx.get("workspace") or "personal"
        return f"open `{ws} › {ctx['path']}` in the panel as subject {rec.get('subject') or '?'}"
    if rec.get("reporter") == "person":
        surf = (ctx.get("surface") or {})
        where = surf.get("path") or surf.get("chat") or rec.get("session") or "the terminal"
        return f"reproduce from the person's seat at {where}"
    return "no reproducible call was recorded — read the logs below from the report's timestamp"


# ── the status machine ───────────────────────────────────────────────────────────────────────────

def apply_report(existing: dict | None, incoming: dict, *, now: float | None = None) -> dict:
    """Fold a new report into whatever the store already holds for its dedup key.

    Three transitions, and the third is the whole reason status exists:

      * nothing yet → `open`, recurrence 1.
      * `open` (or `recurring`) again → recurrence + 1, newest wording wins, status unchanged. The
        wording is REPLACED rather than appended because the last reporter had the most context;
        the count is what carries "this keeps happening".
      * `fixed`, then reported again → `recurring`, and `regressed_at` is stamped. `fix_ref` is
        KEPT: the question a recurring row answers is "which fix did not hold", and dropping the
        reference destroys exactly that.

    `first_at` never moves. It is the age of the defect, and a dump sorted by it is the difference
    between "new since the last pass" and "this has been broken all day"."""
    now = float(now if now is not None else time.time())
    if existing is None:
        rec = dict(incoming)
        rec["status"] = "open"
        rec["recurrence"] = 1
        rec["first_at"] = rec.get("at") or now
        rec["fix_ref"] = ""
        return rec
    rec = dict(existing)
    for k in ("tried", "happened", "would_help", "context", "reporter", "session", "subject",
              "severity", "log_refs"):
        if incoming.get(k):
            rec[k] = incoming[k]
    rec["at"] = incoming.get("at") or now
    rec["recurrence"] = int(rec.get("recurrence") or 1) + 1
    rec.setdefault("first_at", rec["at"])
    if rec.get("status") == "fixed":
        rec["status"] = "recurring"
        rec["regressed_at"] = rec["at"]
    return rec


def apply_fix(rec: dict, fix_ref: str, *, now: float | None = None) -> dict:
    """Close one record against the thing that fixed it (decision 33 §4).

    `fix_ref` is required and unvalidated on purpose: a commit sha, a PR url, a branch, a sentence.
    What matters is that a human or an agent reading the row can find the change; what shape that
    reference takes is not this module's business. An EMPTY one is refused — a record marked fixed
    with nothing to point at is indistinguishable from a record somebody wanted off the list."""
    if not str(fix_ref or "").strip():
        raise ValueError("a fix needs a reference — a commit, a PR, or a sentence naming the change")
    out = dict(rec)
    out["status"] = "fixed"
    out["fix_ref"] = _clip(fix_ref, 300)
    out["fixed_at"] = float(now if now is not None else time.time())
    return out


# ── the dump ─────────────────────────────────────────────────────────────────────────────────────

def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return "?"


def _ctx_line(rec: dict) -> str:
    ctx = rec.get("context") or {}
    bits = []
    for k in CONTEXT_KEYS:
        v = ctx.get(k)
        if not v:
            continue
        if k == "surface":
            inner = " · ".join(f"{a}={b}" for a, b in list(v.items())[:6] if b)
            if inner:
                bits.append(f"surface({inner})")
            continue
        if k == "error":
            continue                        # the error is the symptom line; not repeated here
        bits.append(f"{k} `{v}`")
    bits.append(f"reporter {rec.get('reporter')}" + (f" (uid {rec['subject']})" if rec.get("subject") else ""))
    if rec.get("session"):
        bits.append(f"session `{rec['session']}`")
    bits.append(f"first {_iso(rec.get('first_at') or rec.get('at'))}, last {_iso(rec.get('at'))}")
    return " · ".join(bits)


def group(records: list[dict]) -> list[dict]:
    """Records → findings, one per likely cause, most-urgent first.

    Order is not by count. `recurring` sorts above everything because a fix that did not hold is the
    only class of row that says the last pass was WRONG, and a fixing agent that reads it late has
    already re-fixed something else. Within a status, the loudest (most occurrences) first, then the
    freshest — a defect nobody has hit since yesterday is genuinely less interesting than one that
    fired ten minutes ago."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        buckets.setdefault(group_key(r), []).append(r)
    out = []
    for (kind, subject), rows in buckets.items():
        rows = sorted(rows, key=lambda r: float(r.get("at") or 0), reverse=True)
        count = sum(int(r.get("recurrence") or 1) for r in rows)
        statuses = {r.get("status", "open") for r in rows}
        status = ("recurring" if "recurring" in statuses
                  else "open" if "open" in statuses else "fixed")
        out.append({"kind": kind, "subject": subject, "status": status, "count": count,
                    "rows": rows, "at": max(float(r.get("at") or 0) for r in rows)})
    rank = {"recurring": 0, "open": 1, "fixed": 2}
    out.sort(key=lambda g: (rank.get(g["status"], 3), -g["count"], -g["at"]))
    return out


HEADER = """# Rough edges — the friction dump

Every row here was filed by the agent or the person who hit it (PRD decision 33). Each finding
below is **symptom → exact context → likely cause → log pointers → repro**, deduplicated, grouped by
likely cause (same kind, same tool or path). The counts are occurrences, not rows.

**What to do with this.** Work the findings top-down; `recurring` first — those are edges a previous
fix did not hold on, and the `fix that did not hold` line names the change to re-read. Two rules:

* **A likely cause is a candidate, not a diagnosis.** It is what the reports agree on, computed —
  nobody looked. Read the logs before you believe it.
* **Close what you addressed, and only that.** `friction_fixed(ids=[…], fix_ref="<commit|PR>")`, or
  `POST /api/friction/<id>/fix {"fix_ref": …}`. A record filed again after a fix flips to
  `recurring` by itself, so closing something you did not fix does not hide it — it just costs the
  next reader a pass.
"""


def render_markdown(records: list[dict], *, since: str = "", status: str = "open",
                    now: float | None = None) -> str:
    """The brief, in the ledger's finding shape — handed to a fixing agent verbatim."""
    now = float(now if now is not None else time.time())
    groups = group(records)
    lines = [HEADER, ""]
    scope = f"status `{status or 'any'}`" + (f", since `{since}`" if since else "")
    lines.append(f"_{len(records)} record(s) → {len(groups)} finding(s) · {scope} · "
                 f"generated {_iso(now)}_")
    lines.append("")
    if not groups:
        lines.append("**Nothing filed in this window.** That is a real answer and not an error — "
                     "but if you have been running the product and this is empty, the reporting "
                     "path itself is the first thing to check.")
        return "\n".join(lines) + "\n"
    for i, g in enumerate(groups, 1):
        head = g["subject"] or "—"
        occ = f"{g['count']} occurrence" + ("s" if g["count"] != 1 else "")
        lines.append(f"## FR-{i} · {g['kind']} · `{head}` — {occ}, {g['status']}")
        lines.append("")
        newest = g["rows"][0]
        lines.append(f"- **Symptom** — {newest.get('happened') or '(not stated)'}")
        if newest.get("tried"):
            lines.append(f"- **Tried** — {newest['tried']}")
        err = (newest.get("context") or {}).get("error")
        if err:
            lines.append(f"- **Error** — `{err}`")
        lines.append(f"- **Exact context** — {_ctx_line(newest)}")
        target = f" on `{head}`" if g["subject"] else ""
        agree = (f"{g['count']} reports agree on kind `{g['kind']}`{target}"
                 if g["count"] > 1 else f"one report so far: kind `{g['kind']}`{target}")
        lines.append(f"- **Likely cause** — {agree}, error text "
                     f"`{error_prefix(newest) or '(none)'}`. "
                     "Candidate, computed from the reports — not a diagnosis.")
        for ref in derived_log_refs(newest):
            lines.append(f"- **Logs** — `{log_command(ref)}`")
        lines.append(f"- **Repro** — {repro_line(newest)}")
        if newest.get("would_help"):
            lines.append(f"- **Reporter's ask** — {newest['would_help']}")
        if g["status"] == "recurring":
            fixes = [r.get("fix_ref") for r in g["rows"] if r.get("fix_ref")]
            lines.append(f"- **Fix that did not hold** — {fixes[0] if fixes else '(unrecorded)'}")
        elif g["status"] == "fixed":
            fixes = [r.get("fix_ref") for r in g["rows"] if r.get("fix_ref")]
            lines.append(f"- **Fixed by** — {fixes[0] if fixes else '(unrecorded)'}")
        ids = [r["id"] for r in g["rows"] if r.get("id")]
        if ids:
            arg = ", ".join(f'"{i}"' for i in ids)
            lines.append(f'- **Close with** — `friction_fixed([{arg}], "<commit|PR>")`')
        if len(g["rows"]) > 1:
            lines.append(f"- **Also in this finding** — {len(g['rows']) - 1} other record(s) with "
                         "the same kind and target, different error text")
        lines.append("")
    return "\n".join(lines) + "\n"
