"""THE CLAIM BOOK — what an agent believes about a company, and what a human said about it.

An agent cannot promote its own guess. Anything researched or inferred is PROPOSED here; it becomes
company context only when a person answers and the answer is recorded. That rule was already the
product's; what was wrong was where it ran.

`propose`, `validate`, `company_context` and `mark_scaffolded` were four tool bodies in the control
MCP, each reading and writing `_pending/claims.json` — the read over `GET /api/workspace/file`, the
WRITE over `docker exec -i vexa-dogfood-agent-api-1 sh -c 'cat > /workspaces/…'` (seam inventory
B6.1). So the state machine lived in the MCP and the file lived here, and the only way the MCP could
write it was to reach into this service's container with a shell. Now the state machine is here too,
beside the file, and the tools forward.

ONE JOIN THAT USED TO BE A THIRD CALL: a human answering IS the workspace becoming ready. Marking it
was a separate `mark_scaffolded()`, which meant a person could answer every question and have
nothing take effect because the last step was forgotten. There was never a decision between those
two, so `record_verdicts` does both.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import queue as queue_mod

CLAIMS_PATH = queue_mod.CLAIMS_PATH
VERDICTS = ("confirmed", "corrected", "rejected")


def _load(workspace: Path) -> dict:
    try:
        book = json.loads((workspace / CLAIMS_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        book = {}
    if not isinstance(book, dict):
        book = {}
    book.setdefault("claims", [])
    return book


def _save(workspace: Path, book: dict) -> None:
    f = workspace / CLAIMS_PATH
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(book, indent=1), encoding="utf-8")


def propose(workspace: Path, batch: list) -> dict:
    """Record claims as PROPOSED. Returns the ids and the exact lines to show the person."""
    book = _load(workspace)
    out = []
    for b in batch:
        if isinstance(b, str):
            b = {"claim": b}
        if not isinstance(b, dict) or not b.get("claim"):
            continue
        cid = "c" + str(len(book["claims"]) + 1).zfill(3)
        book["claims"].append({
            "id": cid, "claim": str(b.get("claim", ""))[:600],
            "source": str(b.get("source", ""))[:300] or "proposed by an agent",
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
    """Record a HUMAN's word on proposed claims, and mark the workspace ready if that was the last
    thing standing between it and being usable."""
    book = _load(workspace)
    by_id = {c["id"]: c for c in book.get("claims", [])}
    out, bad = [], []
    for v in batch:
        vid, vd = (v or {}).get("id", ""), (v or {}).get("verdict", "")
        c = by_id.get(vid)
        if not c:
            bad.append({"id": vid, "error": "no such claim"})
            continue
        if vd not in VERDICTS:
            bad.append({"id": vid, "error": "verdict must be confirmed | corrected | rejected"})
            continue
        c["state"] = "validated" if vd == "confirmed" else vd
        c["verdict"] = vd
        c["human_note"] = str(v.get("note", ""))[:600]
        c["validated_at"] = time.time()
        out.append({"id": vid, "state": c["state"],
                    "usable_as_context": vd in ("confirmed", "corrected")})
    if out:
        _save(workspace, book)
    res: dict = {"recorded": out}
    if bad:
        res["errors"] = bad
    if any(o.get("usable_as_context") for o in out) and not (workspace / ".scaffolded").is_file():
        n_ok = len([c for c in book.get("claims", []) if c.get("state") in ("validated", "corrected")])
        queue_mod.mark_scaffolded(workspace, n_ok)
        res["workspace_ready"] = True
        res["tell_your_person"] = ("One line — noted, write-ups will use it — then offer the next "
                                   "thing. No recap of what you just did.")
    return res


def context(workspace: Path) -> dict:
    """The validated company context — only claims a human has confirmed or corrected.

    Proposed claims are deliberately absent: if it is not here, nobody has stood behind it yet."""
    claims = queue_mod.claims_of(workspace)
    good = [c for c in claims if c.get("state") in ("validated", "corrected")]
    return {
        "validated": [{"id": c["id"], "claim": c["claim"], "verdict": c.get("verdict"),
                       "note": c.get("human_note", "")} for c in good],
        "still_proposed": len([c for c in claims if c.get("state") == "proposed"]),
        "rejected": len([c for c in claims if c.get("state") == "rejected"]),
    }


def scaffold(workspace: Path, group: str = "") -> tuple:
    """Declare the workspace ready. ``(status, body)``.

    REFUSED with nothing validated: marking it ready with an empty context means every artifact
    afterwards is written against nothing and nobody finds out until they read one."""
    ctx = context(workspace)
    if not ctx["validated"]:
        return 409, {"refused": "no validated claims yet", "still_proposed": ctx["still_proposed"],
                     "do": "Ask the person about the proposed claims first."}
    name = queue_mod.mark_scaffolded(workspace, len(ctx["validated"]), group)
    return 200, {"marked": name, "written": True, "validated_claims": len(ctx["validated"]),
                 "note": "Queued post-meeting work will run on its next wake."}
