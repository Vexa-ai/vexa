"""friction.py — THE ROUGH-EDGES RECORD'S SHAPE (PRD decision 33; trimmed for #1510's C5).

Founder, 2026-09-02 16:0xZ: *"we also need to leverage the mcp tool that should collect rough edges
— things that did not work as expected — and dump it in a way that we can just dump that to an
agent that would just fix that (like you are here)."*

This module normalizes a report into ONE shape, and nothing else: no storage, no HTTP, no status
machine, no renderer. Three producers write this shape — the worker (`worker/friction.py`), the
terminal's "Report this" (`clients/terminal/src/surfaces/frictionApi.ts`), and agent-api's own
`POST /api/friction` (`control_plane/routers/friction.py`) — and this file is the only place the
shape is written down, so the three cannot quietly drift into three different records.

── WHY THE STATUS MACHINE AND THE DUMP RENDERER ARE GONE (#1510's C5) ─────────────────────────────
This module used to also own dedup (`dedup_key`), a status machine (`apply_report`/`apply_fix`/
`STATUSES`), log-pointer derivation (`derived_log_refs`/`log_command`/`repro_line`) and a markdown
renderer (`group`/`render_markdown`) — all of it in service of `control_plane/friction.py`'s
`FrictionStore`, a Redis ledger that kept one row per deduplicated edge and moved it through
`open → fixed → recurring`.

That store is deleted. The carrier is now flows' own `POST /friction` (`friction-sink-in-flows`),
which admits ONE ROW PER REPORT — no dedup at admission — and `friction.fixed`
(`core/flows/src/flows_integrations/flows_api.py`'s `POST /friction/{id}/fix`, #1510's C3) closes
one against a read model that folds `friction.fixed` rows into `friction_for_subject`'s output.
Neither of those needed the status machine or the renderer: dedup and "recurring" were properties
of a STORE that collapsed occurrences into rows, and a store that no longer exists cannot be
served by code shaped around it. The rig's `friction_dump` (an operator read over flows, #1510's
C2/C3) has its own small grouping now, over the flows read model's rows, not this module's.

WHAT STAYS: the shape, the normalization that lets three very different reporters write the same
row, and the redaction — because a friction report is durable by design (flows admits it as a fact
on the timeline, forever) and a secret pasted into "what went wrong" must not outlive the session
just because the record moved carriers.
"""
from __future__ import annotations

import re
import time

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

#: The context object's keys, in the order a reporter might supply them. `surface` carries the
#: decision-30 human-surface block when the client has one; it is optional by design.
CONTEXT_KEYS = ("workspace", "path", "meeting_id", "scaffold_id", "tool", "error", "surface")

#: Severity is not in decision 33's shape. It is kept because the rig's `report_friction` has always
#: taken it, dozens of live rows carry it, and dropping an argument is not backwards compatibility.
SEVERITIES = ("blocker", "annoyance", "papercut", "idea")

MAX_TEXT = 900          # per free-text field; a report is a lead, not a transcript
MAX_ERROR = 600


def _clip(v, n: int = MAX_TEXT) -> str:
    """Trim ONE free-text field — and scrub it. Every string a person or an agent writes into a
    friction report passes through here, and a friction record is DURABLE BY DESIGN (flows admits
    it as a fact on the timeline, forever). A credential pasted into "what went wrong" would
    therefore outlive the session and the fix. Shape-based, so it does not depend on anybody
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
    unknown is DROPPED rather than carried: a context key nobody reads is a key the next reader has
    to decide how to render, and this record is read by an agent that will act on it.

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
        "log_refs": log_refs(src.get("log_refs")),
    }
    return rec


# The words a reporter who did not classify actually uses. This is a GUESS and is treated as one:
# it only ever fires when `kind` was absent, and `other` is where an unrecognised report lands. It
# exists because the alternative — every unclassified report in one bucket — makes any grouping over
# these records useless on exactly the reports written in a hurry, which are most of them.
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


# ── log pointers ─────────────────────────────────────────────────────────────────────────────────

def log_refs(given) -> list[dict]:
    """`[{container, since, grep}]` — supplied by the reporter, or nothing.

    A reporter that names its own pointers is believed and they are kept as-is: a worker knows its
    own container id and this function does not. There is no more DERIVED half (the old
    `derived_log_refs`/`log_command` lived here for the dump renderer, which is gone with the store
    — #1510's C5): a record with none simply carries none, and a reader with a fresher idea of the
    deployment's container names can compute its own."""
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
