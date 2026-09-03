"""THE QUEUE — what flows is holding for ONE PERSON (PRD decision 42.2).

*"what is waiting — maybe it's flows?"* (founder, 2026-09-03 07:43Z; agreed.) What is waiting is
the set of pending REACTIONS flows already holds for this subject. Nothing is unioned at the edge:
other domains publish events, and flow definitions decide what waits.

That ruling is what this module is. Three properties follow from it, and each one is a defect the
four-source version had:

1. **It exists in every profile.** Two of the old tool's four sources were agent-api reads, so the
   verb could not ship in the `no-agents` product (decision 40.6) — it would be absent from most of
   the eight domain configurations, or present and answering half. A read over `reaction` is a read
   over flows' own table, and flows is in every profile.

2. **A short queue is no longer ambiguous.** Every item names the FLOW that produced it and carries
   a TYPED reason, so *"nothing is waiting"* and *"that part of the machine is not deployed here"*
   are different answers rather than the same empty list. `not_present` is in the vocabulary for
   exactly that: a reaction the engine terminated because a domain was absent (`flows/model.py`
   `NotPresent`) is something the person is owed a sentence about, not something to hide.

3. **The words are not here.** Everything a person hears is resolved from `behavior/queue/`, the
   same private-mount-then-showcase order `flows_defs/production.py` already reads prompts through.
   A tool body holding two hundred lines of product copy is a rebuild and a deploy away from every
   rewrite, which is the thing PRD §3.8 exists to stop.

**THE COPY IS ALSO THE FILTER, and that is the load-bearing idea.** A person's reactions include a
great deal of plumbing — an `invite_intake` parked at `await_start` three hours before a call is
pending, and is nobody's business. Rather than a keyword list in a tool deciding what is
interesting, a pending reaction is SPOKEN when behavior has something to say about it and COUNTED
when it does not. Which reactions reach a person is then an admin's file, editable without a
deploy, and the machinery holds no opinion about it at all.

**The friction first-run ask is dropped** (ruling 8/9, founder 2026-09-03 08:53Z): there is no
`friction.first_run` event, no flow and no copy file for one — *a flow that fires once per person
is a heavy way to say hello*. The enforcement is not this comment: every item this module emits
carries the `reaction_id` it came from, so an item nobody's reaction produced cannot be built here.
"""
from __future__ import annotations

import os
import pathlib
import re
import time
from typing import Callable, Optional

from flows_timeline import concerns, resolve_identity
from flows_timeline.model import loads
from flows_timeline.service import SCAN_ROWS

#: The typed reason vocabulary. Derived from the reaction's own STATUS and from the prefix the
#: engine writes — never from a keyword list over free text, which is what the rig's tool did and
#: what made "smtp" and "timeout" load-bearing English inside a Python file.
TYPE_HUMAN = "human"                 # blocked: a person has to answer before anything moves
TYPE_FAILED = "failed"               # failed, with a reason worth an eye
TYPE_NOT_PRESENT = "not_present"     # terminal because a domain is not deployed (decision 40.7)
TYPE_PENDING = "pending"             # in flight, nothing owed by anybody

#: Reactions that have not finished, or finished badly. `done` is deliberately absent here and
#: handled separately below: an ordinary completion is not waiting for anyone.
UNFINISHED = ("admitted", "running", "retrying", "blocked", "failed")

#: How far back a terminal `not_present` reaction still counts as something to say. Without a
#: horizon the queue would grow monotonically on a deployment that simply does not run a domain —
#: the person would be told the same absence forever, which is noise, not information.
NOT_PRESENT_WINDOW_S = 86_400

#: What `NotPresent.reason` looks like on the row: `"<domain>:not_present"`, optionally followed by
#: `" — <detail>"` (`flows/model.py`). Matching the SHAPE the engine writes rather than searching
#: for a word is what keeps this structural: if the spelling changes, this stops matching loudly
#: instead of quietly classifying a failure as a deployment fact.
_NOT_PRESENT = re.compile(r"^([a-z][a-z0-9_]*):not_present(?:\s+—\s+(?P<detail>.*))?$", re.S)

_COLS = ("reaction_id", "flow", "flow_version", "step", "status", "attempt", "reason",
         "next_run_at", "created_at", "updated_at", "subject_refs")


def typed_reason(status: str, reason: Optional[str]) -> dict:
    """The reason, as data. STRUCTURAL: the status and the engine's own prefix decide the type."""
    text = str(reason or "").strip()
    m = _NOT_PRESENT.match(text)
    if m:
        return {"type": TYPE_NOT_PRESENT, "domain": m.group(1),
                "detail": (m.group("detail") or "").strip()}
    if status == "blocked":
        return {"type": TYPE_HUMAN, "detail": text}
    if status == "failed":
        return {"type": TYPE_FAILED, "detail": text}
    return {"type": TYPE_PENDING, "detail": text}


def _rows(db, sql: str, params: dict) -> list[dict]:
    return [dict(zip(_COLS, r)) for r in db.execute(sql, params)]


def pending(db, *, subject: str, now: Optional[float] = None, limit: int = 50,
            scan: int = SCAN_ROWS, identity: Optional[Callable] = None):
    """This subject's unfinished reactions, plus the recently-absent ones. ``None`` when nobody
    answers to ``subject``.

    FAILS CLOSED, like `flows_timeline.list_reactions` and for the same reason: an unresolvable
    subject that fell through to the unscoped read is how a scoping bug becomes the leak the scope
    was added to close (R-D07, R-D12).

    Scoping is on the uid AND the email — see `flows_timeline.model.concerns`: the invite lineage
    carries an organizer address and no uid, the completed lineage carries a uid and no address,
    and matching on one of them silently returns half of a person's day.

    The SCAN is bounded and the filter is in Python, the shape `list_reactions` and `read_flows`
    already use: `subject_refs` is a JSON blob with no index to push a predicate into.
    """
    now = time.time() if now is None else float(now)
    uid, email = (identity or resolve_identity)(subject)
    if not uid and not email:
        return None
    states = ",".join(f"'{s}'" for s in UNFINISHED)      # a fixed literal set, never caller input
    rows = _rows(db, f"SELECT {', '.join(_COLS)} FROM reaction "
                     f"WHERE status IN ({states}) "
                     f"   OR (status = 'done' AND reason IS NOT NULL AND updated_at >= :floor) "
                     f"ORDER BY updated_at DESC LIMIT {int(scan)}",
                 {"floor": now - NOT_PRESENT_WINDOW_S})
    out = []
    for r in rows:
        if not concerns(loads(r["subject_refs"]), uid, email):
            continue
        reason = typed_reason(r["status"], r["reason"])
        # A `done` row is only here if it is a deployment fact. Anything else that finished is
        # finished, and telling a person about it is telling them about our plumbing.
        if r["status"] == "done" and reason["type"] != TYPE_NOT_PRESENT:
            continue
        out.append({k: v for k, v in r.items() if k != "subject_refs"} | {"reason": reason})
    return out[:limit]


# ── the words, which are behavior's ───────────────────────────────────────────────────────────
#: The behavior tree's directory for this surface. `<flow>.<reason type>.md` first, then
#: `_<reason type>.md` — `behavior/queue/README.md` is the contract.
KIND = "queue"


def _roots() -> list[pathlib.Path]:
    """Private mount first, then the in-repo showcase — the order `flows_defs.production._prompt`
    already reads prompts through, minus its `_global` half.

    The `_global` half is deliberately absent: it is fetched over agent-api, and this queue must
    answer in a deployment that has no agent domain. A copy lookup that needed the agent door would
    reintroduce the exact coupling decision 42.2 removed.
    """
    out = []
    private = (os.environ.get("VEXA_BEHAVIOR_DIR") or "").strip()
    if private:
        out.append(pathlib.Path(private) / KIND)
    here = pathlib.Path(__file__).resolve()
    # checkout: <root>/core/flows/src/flows_queue.py; the image is shallower (/app/src/…)
    candidates = [pathlib.Path("/"), pathlib.Path("/app")]
    if len(here.parents) > 3:
        candidates.append(here.parents[3])
    out += [c / "behavior" / KIND for c in candidates]
    return out


def say(flow: str, reason_type: str) -> str:
    """What a person hears about this item, or "" when behavior has nothing to say about it.

    "" IS AN ANSWER, and it is the filter (see the module docstring): a pending reaction behavior
    is silent about is counted, never spoken. Adding `behavior/queue/<flow>.<type>.md` is how an
    admin makes one situation of one flow person-facing, and deleting it is how they stop it — no
    deploy either way.

    The flow-and-type key rather than a flow key: *"the write-up is being prepared"* and *"the
    write-up failed"* are the same flow and opposite sentences, and a file keyed on the flow alone
    would say the first one to somebody the second had happened to.
    """
    for name in (f"{flow}.{reason_type}.md", f"_{reason_type}.md"):
        for root in _roots():
            f = root / name
            try:
                if f.is_file():
                    text = f.read_text(encoding="utf-8").strip()
                    if text:
                        return text
            except OSError:                # an unreadable mount is not a reason to fail a read
                continue
    return ""


def waiting(db, *, subject: str, flows: Optional[list] = None, now: Optional[float] = None,
            limit: int = 50, identity: Optional[Callable] = None,
            copy: Optional[Callable] = None) -> dict:
    """THE PROJECTION the route serves. Data plus behavior's words — no product opinion here.

    `flows` is what this deployment could ever react to, and it is in the answer for the reason
    § 9 of the destination design gives: it is what makes a short queue legible. "Nothing is
    waiting" against a list that has no `live_meeting` in it says something different from the same
    empty queue against a list that has one, and neither answer needs a field the tool invents.
    """
    rows = pending(db, subject=subject, now=now, limit=limit, identity=identity)
    if rows is None:
        return {"subject": subject, "unresolved": True, "waiting": 0, "items": [],
                "quiet": 0, "flows": flows or []}
    speak = copy or say
    items, quiet = [], 0
    for r in rows:
        words = speak(r["flow"], r["reason"]["type"])
        if not words:
            quiet += 1
            continue
        items.append({"id": r["reaction_id"], "flow": r["flow"],
                      "flow_version": r["flow_version"], "step": r["step"],
                      "status": r["status"], "reason": r["reason"],
                      "since": r["updated_at"], "next_run_at": r["next_run_at"],
                      "say": words})
    return {"subject": subject, "waiting": len(items), "items": items,
            # COUNTED, NOT HIDDEN: an operator asking why a queue is short must be able to tell
            # "nothing is happening" from "behavior is silent about what is happening".
            "quiet": quiet, "flows": flows or []}
