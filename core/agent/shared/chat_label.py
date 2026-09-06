"""chat_label.py — THE ONE RULE THAT NAMES A CHAT (Vexa-ai/vexa#1602).

The founder's rail, 2026-09-06 12:50Z, after #1591 made it the server's sessions rather than one
browser's storage. Eleven rows. Four of them read `Active context: the u…`, and beside those
`[vexa-job:extend…`, `[minutes-review…` and `[prep] They click…`. **A person never typed any of
that.** A server-derived row was labelled with the session's FIRST USER TEXT, and the first user
text of most sessions is machinery: the terminal's "Active context: the user is viewing…" preamble,
a job mark, an ask's `[kind]` prefix.

#1588 ruled on exactly this defect one surface along — pressing Extend showed the founder the whole
`[extend]` preset back as a grey bubble in his own voice, and `marks.act_label` turned the job mark
into `Extend: <path>`. This module is that discipline for every row, in one place, because the rail
is the one thing every client renders and three clients guessing at it is three answers.

── THE RULE, IN ORDER ───────────────────────────────────────────────────────────────────────────
  1. **the MEETING'S TITLE**, for a chat born as a meeting's (`meet-<row>`);
  2. **the SCAFFOLD'S LABEL** — `label:` in the ask's frontmatter ("company setup", "welcome");
  3. **the ACT LABEL**, for a chat an act opened (`marks.act_label` — `Extend: <path>`);
  4. else **THE PERSON'S OWN FIRST WORDS**, with every machinery preamble stripped: the "Active
     context…" block, the marks, the `[kind]` prefixes.
**Never a bracket, never a mark, never "Active context"** — and `is_machinery_label` is that
sentence as a predicate, applied to the ANSWER, so a preamble this file has not met yet costs a row
its name rather than painting machinery on it.

WHY 1 IS ONLY THE `meet-<row>` CASE, and it is not an oversight. A chat that CREATED a meeting
keeps its own name (Vexa-ai/vexa#1597, founder: *"we already have meeting owner, just attach the
status to it"*) — it was a conversation before it was a meeting and the person's own sentence named
it. Only a chat that IS a meeting's, from its id, is named by the meeting.

WHY THIS FILE READS NOTHING. The library (`_global/asks/`), the meetings domain and the scaffold
store all live behind the control plane; a pure rule can be handed their answers and cannot be
handed a filesystem. So the caller resolves `meeting_title` and `scaffold_label` and this composes.
`preset_kind` exists for the same reason in reverse: it says WHICH ask composed a prompt so the
caller can look that ask's `label:` up in the library it owns.

WHY A TRUNCATED MARK IS SALVAGED HERE AND NOT IN `marks.py`. The stored title of a row minted before
this rule is `_truncate_title(prompt)` — 60 characters, single-lined — so `[vexa-job:extend:personal/
kg/entities/person/james-spadafo…` reaches us with the mark's closing `]` cut off. `marks.read_job_
mark` is the RECORD reader and must keep refusing a malformed mark; `act_from_title` below is the
DISPLAY reader for a string that has already been cut, and it is the only reason the founder's
Extend row can read `Extend: personal/kg/entities/person/james-spadafo…` instead of nothing.

WHAT IS NOT RECOVERABLE, said out loud. A row whose stored title is `Active context: the user is
viewing the workspace file kg/e…` was truncated BEFORE the person's words — the preamble alone is 55
characters. There is nothing human left in the string, so the label is empty and the client renders
its own placeholder. Those rows re-title themselves on their next turn (`routers/chats.py` re-titles
a machinery title), and every row minted from now on is named from the whole prompt, before the cut.
"""
from __future__ import annotations

import re

from shared.marks import JOB_MARK, MACHINERY_MARK, PHASE_MARK, act_label, job_mark

#: How long a label may be. The rail is 248px wide and cuts at 48 of its own; this is the index's
#: long-standing 60 (`api_shared._truncate_title`), kept so a stored title and a computed label are
#: the same length and a row does not change width the first time it is recomputed.
CHAT_LABEL_MAX = 60

# The terminal's own narration of what the reader has open, prepended CLIENT-side to the prompt
# (`clients/terminal/src/surfaces/chat.tsx` — `activeContextPrompt`), so it arrives inside
# `body.prompt` and lands in the title. Two separators because a stored title has been through
# `_truncate_title`, which single-lines it: `\n\n---\n` in a live prompt, ` --- ` in a stored one.
_ACTIVE_OPENS = ("Active context", "Active meeting")
_ACTIVE_BLOCK = re.compile(r"\AActive (?:context|meeting)\b[\s\S]*?(?:\n\n---\n|(?<=\s)---(?=\s))")

# An ask's kind, as its body opens: `[prep] They clicked through…`. Lowercase, hyphenated, closed —
# a bracket that opens something else (a person writing `[note] …`) is simply another kind and is
# treated the same way, which is right: the rule is that a row never SHOWS a bracket.
_KIND_PREFIX = re.compile(r"\A\[([a-z][a-z0-9_-]{0,31})\]\s*")

# The job mark as a TRUNCATED string may carry it: opened, kind, target, and a `]` that may have
# been cut off the end. `marks._JOB_RE` requires the close, deliberately. Built from `JOB_MARK` so
# the literal still has exactly one writer.
_JOB_CUT = re.compile(re.escape(JOB_MARK) + r"([a-z0-9_-]{1,32}):([^\]]{0,512})(?:\]|\Z)")


def truncate_label(text: str, *, limit: int = CHAT_LABEL_MAX) -> str:
    """One line, cut with an ellipsis — the same shape the index has always stored."""
    one = " ".join((text or "").split())
    return one[: limit - 1] + "…" if len(one) > limit else one


def is_machinery_label(label: str) -> bool:
    """The founder's sentence as a predicate: *never a bracket, never a mark, never "Active
    context"*.

    Applied to the ANSWER rather than only to the input, so a preamble nobody here has met yet
    costs a row its name instead of putting machinery on the rail. An empty string is not machinery
    — it is "no name", which the caller answers with its own placeholder."""
    t = (label or "").strip()
    if not t:
        return False
    if t.startswith("[") or t.startswith(JOB_MARK):
        return True
    if MACHINERY_MARK in t or PHASE_MARK in t:
        return True
    return any(t.startswith(p) for p in _ACTIVE_OPENS)


def preset_kind(text: str) -> str:
    """The ask that composed this prompt — `prep`, `minutes-review`, `first-visit` — or `""`.

    An ask's body opens with its own kind in brackets and the preset file is named after it, so this
    is how a row whose scaffold id was never sent (every chat minted before `MinutesShell` started
    riding the id onto the first turn) can still be named by the record it came from. The caller
    reads `label:` out of that preset; nothing here knows what the library holds."""
    m = _KIND_PREFIX.match((text or "").lstrip())
    return m.group(1) if m else ""


def act_from_title(text: str) -> str:
    """The act label, tolerant of a stored title whose mark was cut in half — see the module head.

    Tries the record reader first so a whole prompt is read by the one function that owns that job,
    and only then salvages."""
    label = act_label(text or "")
    if label:
        return label
    m = _JOB_CUT.search(text or "")
    if not m:
        return ""
    kind, target = m.group(1).strip().lower(), m.group(2).strip()
    # `act_label` on a REPAIRED mark, so the verb table stays in one place: a kind this build does
    # not know renders the same way there as it does for a mark that arrived whole.
    return act_label(job_mark(kind, target)) or ""


def human_head(text: str) -> str:
    """The person's own words at the head of a composed prompt — every machinery preamble removed.

    Returns `""` when there are none, which is an answer: a prompt that is machinery end to end has
    no human head, and inventing one from the machinery's prose is the defect this file exists for."""
    s = str(text or "")
    if any(s.lstrip().startswith(p) for p in _ACTIVE_OPENS):
        m = _ACTIVE_BLOCK.match(s.lstrip())
        # No separator left means the block itself was truncated — everything we hold is preamble.
        s = s.lstrip()[m.end():] if m else ""
    for mark in (PHASE_MARK, MACHINERY_MARK):
        s = s.replace(mark, " ")
    s = _JOB_CUT.sub(" ", s)
    while True:
        m = _KIND_PREFIX.match(s.lstrip())
        if not m:
            break
        s = s.lstrip()[m.end():]
    out = " ".join(s.split())
    return "" if is_machinery_label(out) else out


def chat_label(text: str, *, meeting_title: str = "", scaffold_label: str = "",
               limit: int = CHAT_LABEL_MAX) -> str:
    """A row's name, in the founder's order — or `""` when nothing human is recoverable.

    `""` rather than a word of our own: "Chat" is the CLIENT'S placeholder for a name nobody chose
    (`minutes/chats.ts` — `isPlaceholderLabel`), and a server that shipped it would be handing every
    client a name that outranks the person's own local rename in the merge."""
    for candidate in (meeting_title, scaffold_label, act_from_title(text)):
        picked = truncate_label(candidate, limit=limit)
        if picked and not is_machinery_label(picked):
            return picked
    return truncate_label(human_head(text), limit=limit)
