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

SCOPE, deliberately narrow: this module PROPOSES and nothing else. `validate` / verdicts /
`mark_scaffolded` remain the rig's for now — they are a human's word on a claim, they belong with
the desk-ready join, and moving them is a separate change with its own event. What is here is what
`claim.proposed` needs to exist.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: Where the book lives on a desk. The SAME path the rig has always written and the same one flows'
#: `await_claim` reads (`flows_defs/production.py` CLAIM_BOOK) — this change moves who writes it,
#: never where it is, so an existing desk's book is still its book.
CLAIMS_PATH = "_pending/claims.json"

MAX_CLAIM_CHARS = 600
MAX_SOURCE_CHARS = 300


def _load(workspace: Path) -> dict:
    """The book, or an empty one. An unreadable or malformed book is treated as empty rather than
    raised on: it is a person's own desk file, it can be edited by hand, and refusing to record
    what an agent just learned because an old file will not parse loses the new fact to protect the
    broken one."""
    try:
        book = json.loads((workspace / CLAIMS_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        book = {}
    if not isinstance(book, dict):
        book = {}
    book.setdefault("claims", [])
    if not isinstance(book["claims"], list):
        book["claims"] = []
    return book


def _save(workspace: Path, book: dict) -> None:
    f = workspace / CLAIMS_PATH
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(book, indent=1), encoding="utf-8")


def propose(workspace: Path, batch: list) -> dict:
    """Record claims as PROPOSED. Returns the new ids, the whole book's view of them, and the exact
    lines to show the person.

    The ids are positional (`c001`, `c002`, …) and that is load-bearing rather than incidental: a
    claim's id is what a queue card is keyed on, so it must be stable for the life of the book and
    must never be reused. Appending only — nothing here ever removes a row.

    Returns `written_ids` alongside `ids` for the route above, which publishes one fact per NEW
    claim and must not re-announce the ones already in the book."""
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
