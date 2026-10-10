"""THE CLAIM BOOK — what an agent believes about a person's company, and what a human said about it.

An agent cannot promote its own guess. Anything researched or inferred is PROPOSED here; it becomes
company context only when a person answers and the answer is recorded. That rule was already the
product's; what is wrong is WHERE IT RUNS. Today the book is written by the rig's `propose` tool
through agent-api's GENERIC file route (`PUT /api/workspace/file`) — so agent-api holds the bytes
and knows nothing about what they mean, and the one moment worth telling anybody about, a claim
being proposed, is indistinguishable from any other file write.

That is why `claim.proposed` had no producer. A generic route cannot publish a specific fact
without inspecting paths and guessing at contents, which is how a file route becomes a state
machine nobody declared. So the state machine moves here, beside the file, and the route above it
publishes exactly one fact per claim.

BOTH HALVES LIVE HERE: an agent PROPOSES, and a person's answer is recorded as VERDICTS. The first
verdict a person stands behind is also the desk becoming ready (`.scaffolded`) — answering IS the
setup, and a separate third step was a step somebody could forget.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from workspaces.shared import workspace_paths as wpaths

#: Where the book lives on a desk. The SAME path the rig has always written and the same one flows'
#: `await_claim` reads (`flows_defs/production.py` CLAIM_BOOK) — this change moves who writes it,
#: never where it is, so an existing desk's book is still its book.
CLAIMS_PATH = "_pending/claims.json"

MAX_CLAIM_CHARS = 600
MAX_SOURCE_CHARS = 300
MAX_NOTE_CHARS = 600

#: The marker a finished setup leaves on the desk — the same file `flows_steps.common.scaffolded`
#: reads to decide a desk is waiting for nothing.
READY_MARKER = ".scaffolded"
#: A person's word on a claim -> the state it leaves the claim in.
VERDICTS = {"confirmed": "validated", "corrected": "corrected", "rejected": "rejected"}
#: The states whose claim may be used as company context: a person stood behind it.
USABLE = frozenset({"validated", "corrected"})


def _load(workspace: Path) -> dict:
    """The book, or an empty one. An unreadable or malformed book is treated as empty rather than
    raised on: it is a person's own desk file, it can be edited by hand, and refusing to record
    what an agent just learned because an old file will not parse loses the new fact to protect the
    broken one."""
    # The desk is a work tree the model's tools can write: the book is read nofollow, so a link at
    # `_pending` or the file is an empty book, never somebody else's.
    try:
        book = json.loads(wpaths.read_text_inside(workspace, CLAIMS_PATH) or "{}")
    except Exception:  # noqa: BLE001
        book = {}
    if not isinstance(book, dict):
        book = {}
    book.setdefault("claims", [])
    if not isinstance(book["claims"], list):
        book["claims"] = []
    return book


def _save(workspace: Path, book: dict) -> None:
    wpaths.write_text_inside(workspace, CLAIMS_PATH, json.dumps(book, indent=1))   # nofollow


def propose(workspace: Path, batch: list) -> dict:
    """Record claims as PROPOSED. Returns the new ids, the whole book's view of them, and the exact
    lines to show the person.

    The ids are positional (`c001`, `c002`, …) and that is load-bearing rather than incidental: a
    claim's id is what a queue card is keyed on, so it must be stable for the life of the book and
    must never be reused. Appending only — nothing here ever removes a row.

    `ids` is exactly the claims THIS call appended — the book is append-only and nothing here
    ever rewrites a row, so the new ids and the published ids are the same list by construction.
    That is what lets the route above publish one fact per NEW claim without re-announcing the
    ones already in the book, and it is the reason there is no second list to keep in step."""
    book = _load(workspace)
    out = []
    for b in batch:
        if isinstance(b, str):
            b = {"claim": b}
        if not isinstance(b, dict) or not str(b.get("claim") or "").strip():
            continue
        cid = "c" + str(len(book["claims"]) + 1).zfill(3)
        book["claims"].append({
            "id": cid, "claim": str(b.get("claim", ""))[:MAX_CLAIM_CHARS],
            "source": str(b.get("source", ""))[:MAX_SOURCE_CHARS] or "proposed by an agent",
            "scope": b.get("scope", "tenant"), "state": "proposed",
            "proposed_at": time.time()})
        out.append(cid)
    _save(workspace, book)
    # Hand back the finished lines rather than a rule about how to write them. Formatting
    # instructions carried in a response are a step, and a step is where a smaller model produces a
    # numbered form or a paragraph — a wall nobody corrects.
    shown = "\n".join("· " + c["claim"] for c in book["claims"][-len(out):]) if out else ""
    return {
        "ids": out, "state": "proposed", "written": True,
        "show_them_exactly_this": ("Here is what I think I understand about your work — correct "
                                   "anything that is wrong.\n" + shown),
        "then": ("Whatever they answer, however brief, goes back in ONE "
                 "validate(verdicts=[{id, verdict, note}]) call. That call finishes the setup."),
        "note": "None of this counts as company context until a human has answered.",
    }


def record_verdicts(workspace: Path, batch: list) -> dict:
    """Record a PERSON's word on proposed claims: `confirmed`, `corrected` (the original stays, the
    correction is the note) or `rejected`. One call carries the whole answer.

    A bad item — an id the book does not hold, a verdict that is not one of the three — is reported
    in `errors` and the rest of the answer still lands: a person who answered five questions in one
    sentence must not lose four of them to a typo in the fifth.

    The first claim a person stands behind makes the desk READY: `.scaffolded` is written once and
    never rewritten, because a desk that is ready stays ready."""
    book = _load(workspace)
    by_id = {str(c.get("id")): c for c in book["claims"] if isinstance(c, dict)}
    recorded, errors = [], []
    for v in batch:
        vid = str(v.get("id") or "").strip()
        verdict = str(v.get("verdict") or "").strip()
        claim = by_id.get(vid)
        if claim is None:
            errors.append({"id": vid, "error": "no such claim"})
            continue
        if verdict not in VERDICTS:
            errors.append({"id": vid, "error": "verdict must be confirmed | corrected | rejected"})
            continue
        claim.update(state=VERDICTS[verdict], verdict=verdict,
                     human_note=str(v.get("note") or "")[:MAX_NOTE_CHARS],
                     validated_at=time.time())
        recorded.append({"id": vid, "state": claim["state"],
                         "usable_as_context": claim["state"] in USABLE})
    if recorded:
        _save(workspace, book)
    out: dict = {"recorded": recorded}
    if errors:
        out["errors"] = errors
    if (any(r["usable_as_context"] for r in recorded)
            and not wpaths.is_file_inside(workspace, READY_MARKER)):
        usable = sum(1 for c in book["claims"] if isinstance(c, dict) and c.get("state") in USABLE)
        wpaths.write_text_inside(workspace, READY_MARKER,
                                 json.dumps({"ready": True, "at": time.time(),
                                             "validated_claims": usable}))
        out["workspace_ready"] = True
        out["tell_your_person"] = ("One line — noted, write-ups will use it — then offer the next "
                                   "thing. No recap of what you just did.")
    return out
