"""THE TWO CHAT MARKS — the literals three images agree on, written once.

A composed opening is not the person's own words: they clicked a link, they did not type a paragraph
of instructions, and on 2026-09-02 the founder saw exactly that paragraph painted as his own chat
message (ledger F7). A write-back phase runs in the SAME harness session as the turn it follows, so
its prompt and its reply land in the transcript the history reader serves, and the founder read those
back as his own conversation too (F51). Both are machinery, and machinery has to be recognisable in
the RECORD rather than guessed at from its prose.

WHY ONE MODULE NOW. Until this file existed the two marks were written FOUR and THREE times, and every
copy carried a comment explaining that the duplication was deliberate because "the sides ship in
different images". That reason is true of the TypeScript copy and was never true of the Python ones:
`core/agent/shared` is COPY'd into agent-api (`services/agent-api/Dockerfile`), into the worker
(`worker/Dockerfile`) and into lite (`deploy/lite/Dockerfile.lite`), and `worker/engine.py` and
`control_plane/workspace_reader.py` already import from it at module scope. Three images were agreeing
on a literal by hand while the module that could hold it was already in all three.

WHAT IS STILL DUPLICATED, DELIBERATELY, AND GATED. `clients/terminal/src/canvas/actions.ts` ships in
the terminal image and cannot import Python. That copy stays — and `gate:fact-parity` compares it to
this file on every push (`scripts/parity.json`, fact `machinery-mark`), which is what the old comments
were hoping a human would notice.

ONE MARK PER MEANING, and they are not interchangeable:
  MACHINERY_MARK  hides the PROMPT bubble — the reply is still shown. A composed opening is machinery
                  whose ANSWER the person read and must keep.
  PHASE_MARK      drops the prompt AND every agent turn up to the next thing a person said. A phase
                  exchange is machinery whose answer nobody was ever shown.
Suppressing everything after a MACHINERY_MARK turn would delete the first real reply of every
scaffolded chat; marking a composed opening with the phase mark would swallow it too.

The worker has historically called the second one WRITEBACK_MARK. That name is kept as an alias
below, so nothing that reads `engine.WRITEBACK_MARK` moves, and there is still exactly one literal.
"""
from __future__ import annotations

#: Hides the prompt bubble; the reply is still rendered.
MACHINERY_MARK = "[vexa-machinery]"

#: Drops the prompt and every agent turn up to the next thing a person actually said.
PHASE_MARK = "[vexa-phase:writeback]"

#: The worker's historical name for PHASE_MARK. One literal, two names, no second source.
WRITEBACK_MARK = PHASE_MARK


# ── the third mark: this act does not hold the chat (Vexa-ai/vexa#1584) ──────────────────────────
# A turn that takes two minutes holds the composer for two minutes. On 2026-09-06 the founder
# pressed Create and Extend four times in the minutes panel; 38 tool calls later he still could not
# ask anything. The act was never the problem — running it INSIDE the turn was.
#
# So the control plane marks such an act on its way past, and the worker runs it as a background
# JOB: the turn returns at once with one line, the job runs on its own thread with its own harness
# session, and its result arrives later as a line and a refreshed page (`worker/jobs.py`, and
# `llm/JOBS.md` for the whole contract).
#
# WHY A MARK AND NOT A FIELD. The same reason the two above are marks: the decision has to be
# recognisable in the RECORD. `_context_grounding` prepends the grounding and the context sentinel
# before the worker ever sees this string, so the mark rides mid-prompt by construction and
# `read_job_mark` SEARCHES rather than matching at the start — reading it strips it in place and
# leaves everything else, so the job runs the whole composed prompt, grounding included.
#
# Unlike MACHINERY_MARK there is no TypeScript copy to keep honest: the server writes this literal
# and the terminal never does.
import re as _re

#: Opens the job mark; the kind and target follow, closed by ``]``.
JOB_MARK = "[vexa-job:"

_JOB_RE = _re.compile(r"\[vexa-job:([a-z0-9_-]{1,32}):([^\]]{0,512})\]\s*")


def job_mark(kind: str, target: str) -> str:
    """The prefix a job act carries. ``target`` is the ONE thing the job acts on — the refusal of a
    second job keys on it, so two acts on one page cannot run at once."""
    return f"{JOB_MARK}{kind}:{target}] "


def read_job_mark(text: str) -> "tuple[str, str, str] | None":
    """``(kind, target, text-without-the-mark)`` when this prompt asks for a job, else None."""
    m = _JOB_RE.search(text or "")
    if not m:
        return None
    return m.group(1), m.group(2).strip(), (text[:m.start()] + text[m.end():])
