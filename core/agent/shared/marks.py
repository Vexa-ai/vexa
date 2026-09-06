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

#: THE SHAPE EVERY MARK BELOW SHARES: two ``:``-separated fields, closed by ``]``. Written once so a
#: reader added later cannot read the fields differently from the writer that composed them.
_MARK_FIELDS = r"([a-z0-9_-]{1,32}):([^\]]{0,512})\]\s*"

#: THE JOB READER, and it stays job-only. ``_TURN_RE`` below reads three namespaces because "what
#: does the person read instead of this" is one question for all of them; "does this spawn a
#: background job" is not, and a display mark able to answer it would run somebody's chip as a job.
_JOB_RE = _re.compile(_re.escape(JOB_MARK) + _MARK_FIELDS)


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


#: How each act reads to the person who pressed it. The verb is the BUTTON's, not the preset's — a
#: label is what the reader recognises as the thing they just did. `extend_transcript` is a separate
#: KIND only because its target is a room and not a file (Vexa-ai/vexa#1596); the person pressed the
#: same Extend and must read the same word back.
#: ``explore`` is here and not in JOB_KINDS on purpose: the chip runs INLINE (it is a lookup, not a
#: 30-120s write), so it never carried a job mark — and therefore carried no mark at all, and the
#: founder read his own chip back as a paragraph he had written (Vexa-ai/vexa#1605).
#: ``policies_wizard`` (Vexa-ai/vexa#1627) is here for the same reason ``explore`` is: it runs
#: INLINE — a wizard is a conversation, not a 30-120s write — so it carries an act mark rather
#: than a job one, and without a verb here the label a reload rebuilds would be `Policies_wizard`.
#: The three membership acts (Vexa-ai/vexa#1632) are here for the same reason ``policies_wizard`` is
#: — they run INLINE, so they carry an act mark rather than a job one, and without a verb here the
#: label a reload rebuilds would read ``Member_add``. The words are the BUTTONS' and match
#: `clients/terminal/src/minutes/extend.ts`'s `VERB`, which is the copy a person reads live; this is
#: the copy they read after a refresh, and the two must not be two.
_ACT_VERBS = {"create": "Create", "extend": "Extend", "extend_transcript": "Extend",
              "explore": "Explore", "policies_wizard": "Set up policies",
              "member_add": "Add a member", "member_role": "Change role",
              "member_remove": "Remove a member"}


# ── the fourth and fifth marks: NOBODY TYPED THIS TURN EITHER (Vexa-ai/vexa#1605) ────────────────
#
# The founder, 2026-09-06 13:15Z, opening a held meeting's chat from the rail: the whole
# `process-meeting` kick — "1) the body — frontmatter-free prose … WRITE NO FILES FOR THIS REPORT …
# Your REPLY is the artefact …" — painted as HIS OWN grey bubble, above the agent's report. Nobody
# was at the keyboard: a FLOW dispatched that turn.
#
# It is #1588's defect one caller along, and #1588's fix could not reach it. An ACT is marked because
# this control plane composed it from a button it was told about; a flow turn is composed in another
# process entirely (`core/flows`), arrives over HTTP at `/api/chat`, and carried nothing that said
# so. `human_half` then did on it exactly what it does on an act — cut at the context sentinel and
# hand back everything after it, which here is the whole instruction — and the chat rendered that as
# speech.
#
# THE RULE IS THE ONE ON THE ISSUE: *a turn nobody typed never renders as the person's words*. So
# every machine-composed turn carries a mark, and there are now three namespaces of ONE shape,
# because the three answer three different questions about the same turn:
#
#   [vexa-job:<kind>:<target>]   this act runs as a BACKGROUND JOB     (#1584 — `read_job_mark`)
#   [vexa-act:<kind>:<target>]   this act runs inline and DISPLAYS as its label          (#1605)
#   [vexa-flow:<flow>:<step>]    a FLOW dispatched this turn, and this is which step      (#1605)
#
# WHO WRITES THE FLOW MARK: agent-api, on the way past, out of the caller's own identity — flows
# already knows its flow and its step (`Reaction.flow` / `Reaction.step`) and now says so in two
# headers. Not flows itself, for the reason the opening of an intent is a NAME and never a string:
# the marks are this control plane's vocabulary, and a caller able to compose one could compose any.
#
# THE MARK IS FURNITURE AND CARRIES NO AUTHORITY — it changes what a bubble reads and nothing else:
# no mount, no job, no permission. That is why it is not gated on the internal-tier secret the ROOM
# is. What IS enforced is the SHAPE: both fields are reduced to `[a-z0-9_-]` before they enter a
# mark, so neither can carry the `]` that would close it early and spill the rest of itself into the
# prompt as instructions — the hazard `chat_intents._passage` names for a selected passage.

#: Opens the display-only act mark; the kind and target follow, closed by ``]``.
ACT_MARK = "[vexa-act:"

#: Opens the flow mark; the flow and the step follow, closed by ``]``.
FLOW_MARK = "[vexa-flow:"

#: Every mark that says NOBODY TYPED THIS — the namespace, then the two fields.
_TURN_RE = _re.compile(r"\[vexa-(job|act|flow):" + _MARK_FIELDS)


def _token(value: str, limit: int = 32) -> str:
    """One field of a mark, reduced to what a mark may carry — see the block above for why."""
    return _re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")[:limit]


def act_mark(kind: str, target: str) -> str:
    """The prefix an act that runs INLINE carries. The same two fields as ``job_mark`` and
    deliberately not that mark: this one must never make the worker take the turn off the chat."""
    return f"{ACT_MARK}{kind}:{target}] "


def flow_mark(flow: str, step: str) -> str:
    """The prefix a flow-dispatched turn carries — or ``""`` when the caller named neither half.

    ``""`` rather than half a mark: a mark with nothing to name is a bracket on somebody's screen,
    which is the thing this file exists to stop."""
    f, s = _token(flow), _token(step, 64)
    return f"{FLOW_MARK}{f}:{s}] " if f and s else ""


def turn_namespace(text: str) -> str:
    """Which mark composed this turn — ``job`` · ``act`` · ``flow`` — or ``""`` for one somebody typed.

    The narrowest possible question about a mark, and it exists so that no caller has to ask it by
    testing for a bracket of its own (Vexa-ai/vexa#1622, where the budget a turn gets depends on
    whether a flow dispatched it). A substring test outside this module is a second reader of a
    shape this module owns, and the TS twin held by `gate:fact-parity` would not know about it."""
    m = _TURN_RE.search(text or "")
    return m.group(1) if m else ""


#: WHAT A MACHINE-COMPOSED TURN IS CALLED — the small table the issue asks for, in one place because
#: three surfaces (the bubble, the record the worker writes, the reader that serves old records)
#: must answer the same way or the founder sees the label change when he reloads.
#:
#: Keyed THREE ways, and each key earns its place:
#:   `<flow>:<step>` — the specific answer;
#:   `<step>`        — because one step runs under more than one flow (`post_meeting` and the gated
#:                     rehearsal flow both run `process_meeting`) and the label is the STEP's;
#:   `<kind>`        — the bracket a composed body opens itself with, which is the ONLY thing left
#:                     to read in a turn dispatched before the marks existed. Those turns are in
#:                     people's transcripts already and are not ours to rewrite.
_TURN_LABELS = {
    # flow steps
    "post_meeting:process_meeting": "Meeting processed",
    "process_meeting": "Meeting processed",
    "feedback_turn": "Email reply",
    "open_person": "Getting you set up",
    "open_group": "Setting up the group",
    # composed bodies, by the kind their first bracket names
    "post-meeting": "Meeting processed",
    "email-reply": "Email reply",
    "prep": "Prepared",
    "minutes-review": "Minutes reviewed",
}


def turn_label(key: str) -> str:
    """The label for a flow step — the pair, else the step alone, else the step's own words.

    THE FALLBACK IS THE REASON THIS NEVER RENDERS A BRACKET. A step this build's table has not met
    reads `Process meeting` rather than nothing, so a flow somebody adds next month is legible on
    the day it ships instead of on the day someone remembers to come back here."""
    k = str(key or "").strip().lower()
    if not k:
        return ""
    hit = _TURN_LABELS.get(k)
    if hit:
        return hit
    tail = k.rsplit(":", 1)[-1]
    hit = _TURN_LABELS.get(tail) or _ACT_VERBS.get(tail)
    if hit:
        return hit
    words = " ".join(tail.replace("-", " ").replace("_", " ").split())
    return words[:1].upper() + words[1:]


def composed_kind_label(kind: str) -> str:
    """The label for a composed body that names its own kind in its first bracket — CLOSED, with no
    fallback, and that is the whole difference from ``turn_label``.

    Who wrote the bracket is what separates them. A flow mark was written by this control plane, so
    an unknown step is still ours and humanising it is safe. A `[kind]` at the head of a prompt was
    written by whoever composed that prompt — and a PERSON may type `[note] remember this`, whose
    words are the one thing that must never be replaced by a label. So this answers only for the
    kinds we know we compose; everything else is somebody's own sentence and stays it."""
    return _TURN_LABELS.get(str(kind or "").strip().lower(), "")


def act_label(text: str) -> "str | None":
    """THE ONE LINE AN ACT RENDERS AS — ``Extend: kg/entities/person/james-spadafora.md`` — or None
    when this prompt is not one.

    THE PERSON SEES THE LABEL, NEVER THE MACHINERY (Vexa-ai/vexa#1588). Pressing Extend sends an
    INTENT; the control plane turns it into the admin-owned preset in ``_global/asks/extend.md``,
    and that composed block is what reaches the transcript as the turn's prompt. The worker records
    the person's half of a turn by cutting at the context sentinel (``human_half``) — a machine
    boundary that is exactly right for a sentence somebody typed and exactly wrong here, because on
    an act there is no sentence: everything after the sentinel is the preset. So the founder pressed
    Extend and read the whole ``[extend]`` body back — its "Expand means EVERY direction" section
    and all — as a grey bubble in his own voice. An earlier Extend, before the intent carried a
    preset, had shown the short label; the words changed and the display followed them.

    The mark the control plane already writes is what makes the label derivable rather than guessed:
    it names the KIND the button was and the TARGET it acts on, in the record, where a reader can
    find it without knowing any English. Nothing here reads the preset.

    Same rule as ``human_half``: this is the DISPLAY half. The prompt is untouched — the agent still
    gets every word of the preset, which is the half that has to be complete.

    AND EVERY OTHER TURN NOBODY TYPED (Vexa-ai/vexa#1605). "What does the person read instead of
    this" is one question, so this reads all three namespaces. A FLOW's answer is not a verb and a
    target — nobody pressed anything — but the step's own name for itself, out of ``_TURN_LABELS``:
    ``[vexa-flow:post_meeting:process_meeting]`` renders *Meeting processed*."""
    m = _TURN_RE.search(text or "")
    if not m:
        return None
    namespace, first, second = m.group(1), m.group(2).strip().lower(), m.group(3).strip()
    if namespace == "flow":
        return turn_label(f"{first}:{second}") or None
    verb = _ACT_VERBS.get(first) or (first[:1].upper() + first[1:])
    return f"{verb}: {second}" if second else verb
