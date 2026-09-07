"""chat_intents.py — a button pressed on a page → the preset that turn runs (PRD decision 32/35).

The terminal sends a small typed record beside the prompt (`surfaces/chatIntent.ts`); this is the
server half. It maps the intent's KIND to a preset name in `_global/asks/` and supplies the tokens
that preset substitutes. Nothing here composes text: the words live in the admin-owned preset file,
for the same reason a scaffold's opening is a NAME and never a string — anyone able to make the
client send an intent would otherwise be able to drive the recipient's agent.

Pure, and its own module, because the mapping is the part worth testing and the route it is called
from needs a running app to exercise.

⚠ THE CLIENT'S FALLBACK SENTENCE ALWAYS TRAVELS TOO (`minutes/extend.ts` — `fallbackText`). When a
preset is missing this returns None, the route leaves `prompt` alone, and the turn still does the
right thing in plainer words. A missing preset must degrade, never 500: the preset library is admin
content and a deployment can legitimately be behind the client.
"""
from __future__ import annotations

# The closed set. A kind outside it is ignored rather than guessed into a preset name — an intent is
# attacker-reachable in exactly the way a scaffold's `opening` is, and `read_preset` refuses a path
# only because nothing ever hands it one.
INTENT_PRESETS: dict[str, str] = {
    "extend": "extend",        # decision 32.2 — go further on the open page / a selection
    "create": "create",        # decision 32.4 — write the page that is not there yet
    "explore": "explore",      # decision 35.3 — a chip in a transcript: find out what this is
    "highlight": "highlight",  # decision 35.2 — publish the terms worth chipping, silently
    # Vexa-ai/vexa#1596 — the same press, on a passage of a MEETING rather than of a file. A
    # separate kind because a transcript has no path: the preset it runs takes a meeting, a speaker
    # and a time where the page one takes a workspace and a path, and one preset asked to serve both
    # would have to guess which half of its own tokens are real.
    "extend_transcript": "extend-transcript",
    # Vexa-ai/vexa#1627 — the **Set up policies** act on the policy page, and the same wizard
    # the setup ask calls by name. NOT in JOB_KINDS and NOT in SILENT_KINDS: it is a
    # conversation with five questions in it, so there is no background job to run, and the
    # person pressed a labelled control, so the turn reads as that label.
    "policies_wizard": "policies-wizard",
    # Vexa-ai/vexa#1632 — the workspace front page's three membership controls. Founder, 2026-09-06:
    # *"this add member should just ask chat to do that with mcp, asking their emails etc."* and
    # *"so we do not have to create UI here — button to trigger the chat."*
    #
    # THREE KINDS AND NOT ONE WITH AN ARGUMENT, for the reason `extend` and `create` are two: the
    # kind is what the person READS back (`shared/marks._ACT_VERBS`), and *Add a member* and
    # *Remove a member* are not the same act with a parameter — they are the two ends of the
    # decision. A single kind would render one label for both and put the wrong word in the
    # transcript of whichever half you did not name it after.
    #
    # NOT in JOB_KINDS: each one opens a QUESTION, and a question that runs on a background thread
    # is a question nobody is there to answer. NOT in SILENT_KINDS: the person pressed a labelled
    # control and must read that label back.
    "member_add": "member-add",
    "member_role": "member-role",
    "member_remove": "member-remove",
    # Vexa-ai/vexa#1639 — writing a flow from the governance chat, and sending a step proposal that
    # came out of one. Founder, 2026-09-06: *"we want to be able to write flows for the global chat
    # as we like."*
    #
    # ONE KIND FOR BOTH, unlike the three membership acts above, and the difference is what a person
    # reads back: *Add a member* and *Remove a member* are the two ends of a decision and must not
    # share a label, while writing a flow and sending the proposal a flow produced are one
    # conversation with the same administrator about the same thing. The ask branches on `path` —
    # a page under `flows/proposals/` is the send, anything else is the authoring.
    #
    # NOT in JOB_KINDS: it opens a question — the one confirmation before a flow goes live — and a
    # question that runs on a background thread is a question nobody is there to answer. NOT in
    # SILENT_KINDS: the person pressed a labelled control and must read that label back.
    "flow_author": "flow-author",
}

# Kinds whose turn the person must NOT see as a bubble. `highlight` is machinery end to end: the
# founder's correction is that pressing the button "silently" requests the terms — a visible
# "Highlight: …" bubble in the conversation would be the product narrating its own plumbing, which
# is the failure MACHINERY_MARK exists to stop one layer up.
SILENT_KINDS = frozenset({"highlight"})


# THE TWO MARKS A SILENT TURN CARRIES, duplicated here for the reason the others are: this module
# is pure and importless by design, the worker ships in its own image, and `test_chat_intents.py`
# pins all four literals together so a rename cannot drift them apart.
#
# BOTH marks, and the ORDER matters as little as the pairing matters a lot:
#   MACHINERY_MARK  hides the PROMPT bubble — the reply is still shown.
#   PHASE_MARK      drops the prompt AND every agent turn up to the next thing a person said.
# A silent intent needs the SECOND. Marking it machinery alone would render the tool calls and the
# prose as a normal exchange; marking a composed OPENING with the phase mark would swallow the first
# real answer of every scaffolded chat. `worker/engine.py` says exactly this at its own definition —
# "Distinct from MACHINERY_MARK and not a replacement for it" — and it is the whole reason there are
# two literals rather than one flag.
# ONE SOURCE, three images (shared/marks.py). Re-exported under the names this module has always
# published, so every reader of `chat_intents.MACHINERY_MARK` is unmoved.
from shared.marks import (MACHINERY_MARK, PHASE_MARK, act_mark,  # noqa: E402 — re-export, see above
                          job_mark)
SILENT_PREFIX = MACHINERY_MARK + " " + PHASE_MARK + " "


# Kinds whose act must NOT hold the chat (Vexa-ai/vexa#1584). Create and Extend read, search and
# write for 30-120s; the founder pressed them four times on 2026-09-06 and the composer was busy
# throughout. They run as background JOBS: the turn returns one line at once, the job runs on its
# own thread in the worker, and its result lands as a line plus a refreshed page tab.
#
# A CLOSED SET, like SILENT_KINDS above and for the same reason: whether an act may spawn a job is
# not a flag the wire gets to set. `explore` and `highlight` stay inline — one is a chip lookup, the
# other is machinery that publishes terms and says nothing.
JOB_KINDS = frozenset({"create", "extend", "extend_transcript"})

#: How much of a selected passage NAMES the job it started. A job's target is read by a person in
#: one line ("Extending meeting 41 · “…” — I'll say when it's there"), so it is a label; the whole
#: passage would be a paragraph inside a chat line, and inside the mark's 512-character room.
TARGET_SELECTION_MAX = 60


def is_job(intent) -> bool:
    """Does this intent run as a background job? Reads the KIND, never a client-supplied flag."""
    if not isinstance(intent, dict):
        return False
    return str(intent.get("kind") or "").strip().lower() in JOB_KINDS


def _passage(selection: str) -> str:
    """A selected passage as a job target can carry it.

    ``]`` CLOSES THE MARK (`shared/marks._JOB_RE` reads `[^\\]]{0,512}`), so a passage carrying one
    would end the mark early and spill the rest of itself into the prompt as instructions. It is
    removed here, at the only place a person's words ever enter a mark. Whitespace is flattened for
    the same reason a label is one line, and the result is capped."""
    flat = " ".join(str(selection or "").replace("]", "").split())
    return flat[:TARGET_SELECTION_MAX].strip() + ("…" if len(flat) > TARGET_SELECTION_MAX else "")


def job_target(intent) -> str:
    """The ONE thing the job acts on — the workspace-qualified page path. The duplicate refusal keys
    on this string, so it has to name the page the same way twice: a bare path in one workspace and
    the same path in another must not collide, and the same page reached twice must."""
    d = intent if isinstance(intent, dict) else {}
    # A TRANSCRIPT PASSAGE HAS NO PATH (Vexa-ai/vexa#1596). What the person acted on is the meeting
    # and the words they highlighted, so those are what the job is named after — which also keeps
    # the refusal honest: two passages of one meeting are two targets and may run at once, while
    # pressing Extend twice on the SAME passage is the double-press the refusal exists for.
    if str(d.get("kind") or "").strip().lower() == "extend_transcript":
        meeting = str(d.get("meeting") or "").strip()
        quote = _passage(d.get("selection"))
        if meeting and quote:
            return f"meeting {meeting} · “{quote}”"
        return f"meeting {meeting}" if meeting else quote
    ws = str(d.get("workspace") or "").strip()
    path = str(d.get("path") or "").strip()
    return f"{ws}/{path}" if ws and path else (path or ws)


def job_prefix(intent) -> str:
    """The mark a job act carries, or ``""``. The route prefixes it exactly as it prefixes
    SILENT_PREFIX — one line there, the closed set and the composition here, where they are tested."""
    if not is_job(intent):
        return ""
    return job_mark(str(intent.get("kind") or "").strip().lower(), job_target(intent))


#: The three membership acts (Vexa-ai/vexa#1632), named once because two functions below ask the
#: same question about them and a second inline tuple is a second answer waiting to disagree.
MEMBER_KINDS = frozenset({"member_add", "member_role", "member_remove"})


def act_target(intent) -> str:
    """What an act that runs INLINE is named after — the page, or for a transcript chip the TERM.

    ``job_target`` answers for the acts that run as jobs, and every one of those acts on a page. The
    chip does not: `explore` carries a meeting, a segment and the clicked word, so ``job_target``
    would answer `""` and the label would read `Explore` with nothing after it."""
    d = intent if isinstance(intent, dict) else {}
    kind = str(d.get("kind") or "").strip().lower()
    if kind == "explore":
        return _passage(d.get("term"))
    # A MEMBERSHIP ACT NAMES A WORKSPACE AND, WHEN THERE IS ONE, A PERSON (Vexa-ai/vexa#1632). There
    # is no path, so `job_target` would fall through to the bare slug and *Change role: pilot* would
    # not say whose. The separator is the transcript act's own ` · `, so the two labels a person
    # sees in one conversation are punctuated alike. `_passage` is what keeps an address out of the
    # mark's closing bracket.
    if kind in MEMBER_KINDS:
        ws = str(d.get("workspace") or "").strip()
        who = _passage(d.get("member"))
        return f"{ws} · {who}" if ws and who else (ws or who)
    return job_target(intent)


def act_prefix(intent) -> str:
    """The DISPLAY mark an inline composed act carries, or ``""`` (Vexa-ai/vexa#1605).

    THE GAP #1588 LEFT, named on its own issue. `explore` composes a whole preset — "[explore] They
    clicked **X** in the transcript of meeting 41 … Write its page with `entity_upsert` …" — and
    carried no mark, because it is not a job and it is not silent, so the person read their own chip
    back as a paragraph they had written. A job kind rides ``job_prefix`` and a silent one rides
    SILENT_PREFIX; this is every other kind of the closed vocabulary, which today is exactly one.

    NEVER THE JOB MARK for these: the worker reads that one to decide whether to run the turn on a
    background thread, and a chip that took itself off the chat because it wanted a label would be a
    strange bug to have to explain."""
    if not isinstance(intent, dict):
        return ""
    kind = str(intent.get("kind") or "").strip().lower()
    if kind not in INTENT_PRESETS or is_job(intent) or is_silent(intent):
        return ""
    return act_mark(kind, act_target(intent))


def is_silent(intent) -> bool:
    """Is this turn one the person must never see as a bubble?

    Reads the KIND, not a client-supplied flag. `intent.silent: true` on the wire would let anyone
    able to mint an intent make a turn invisible in someone else's conversation, which is the same
    capability `opening` is a NAME rather than a string to deny. The closed set is here."""
    if not isinstance(intent, dict):
        return False
    return str(intent.get("kind") or "").strip().lower() in SILENT_KINDS


# THE MEETING-DOC VARIANT (Vexa-ai/vexa#1598). Extend on a page that has a live transcript embedded
# in it is a different act from Extend on any other page: it reads the transcript SINCE A CURSOR the
# page itself carries, it writes into regions rather than wherever it likes, and it must not touch
# the widget slot. That is a different ask, not a different paragraph of the same one.
#
# It is keyed on the intent naming a MEETING, and the client only puts one there when the open page
# DECLARES the widget — so the variant follows the page's own binding rather than the shell's idea
# of what chat is open. A page is either a meeting's page or it is not, and it says which in itself.
INTENT_VARIANTS: dict[str, str] = {
    "extend": "extend-meeting",
}


def preset_for(intent) -> str | None:
    """The preset name this intent runs, or None when there is nothing safe to run."""
    names = presets_for(intent)
    return names[-1] if names else None


def presets_for(intent) -> list[str]:
    """The presets this intent may run, MOST SPECIFIC FIRST — the caller takes the first that reads.

    A chain rather than a name because `_global/asks/` is admin-owned and `preset_library.top_up` is
    additive: a deployment that predates the meeting variant simply does not have the file, and the
    right degradation there is the ordinary `extend` ask, not the client's plain fallback sentence.
    Returning a name that may not exist and letting the route fall to `body.prompt` would make the
    act WORSE on an older library than it was before the variant was written, which is a strange
    thing for a new feature to do to instances that have not taken it."""
    if not isinstance(intent, dict):
        return []
    kind = str(intent.get("kind") or "").strip().lower()
    base = INTENT_PRESETS.get(kind)
    if not base:
        return []
    variant = INTENT_VARIANTS.get(kind) if str(intent.get("meeting") or "").strip() else None
    return [variant, base] if variant else [base]


def tokens_for(intent) -> dict:
    """`{{token}}` values for the preset body.

    Every value is a plain string and every absent one is "" rather than the word None — an unknown
    token is left standing by `substitute` so an admin sees their typo, but a token that IS known
    and empty must render as nothing, not as the literal `None` the founder would then read in his
    own chat."""
    d = intent if isinstance(intent, dict) else {}

    def s(key: str) -> str:
        v = d.get(key)
        return "" if v is None else str(v)

    return {
        "kind": s("kind"),
        "path": s("path"),
        "workspace": s("workspace"),
        "selection": s("selection"),
        # THE PERSON'S OWN LINE (Vexa-ai/vexa#1593). The one token whose value is text a human typed
        # rather than a fact about their screen — see `with_instruction` below for why that is
        # allowed here and nowhere else on this record.
        "instruction": s("instruction"),
        "term": s("term"),
        "meeting": s("meeting"),
        "segment": s("segment"),
        "since": s("since"),
        # WHERE IN THE ROOM a passage was said (Vexa-ai/vexa#1596) — the transcript's answer to
        # `path`. Both are empty when the client could not establish them exactly, and an empty one
        # renders as nothing: a preset that says "said by {{speaker}}" over an unknown speaker
        # would be the placeholder-spoken-with-confidence failure the docstring above names.
        "speaker": s("speaker"),
        "at": s("at"),
        # WHO A MEMBERSHIP ACT IS ABOUT (Vexa-ai/vexa#1632) — the address the roster row showed, or
        # the subject when it had no address. Empty on `member_add`, which has nobody yet: that is
        # the act whose whole first move is to ASK, and a preset that read a name here would be
        # answering its own question.
        "member": s("member"),
    }


# ── the person's own line (Vexa-ai/vexa#1593) ────────────────────────────────────────────────────
#
# Founder, 2026-09-06, with "recorded YouTube video" selected on a page: *"extend might have an
# extra prompt that opens on click like 'find link on youtube i would add then'"*. The selection is
# the WHERE; this is the WHAT. It rides the intent as `instruction` and reaches the preset as
# `{{instruction}}`.
#
# ⚠ WHY THIS MODULE COMPOSES A SENTENCE, HAVING SAID IT NEVER WOULD. The header's rule is that the
# WORDS are admin-owned, because anyone able to make a client send an intent could otherwise drive
# the recipient's agent. That rule is about OUR words. This is the PERSON'S OWN TEXT, typed into
# their own chat, addressed to their own agent — exactly what the composer one panel away already
# sends, and no new capability at all. What is composed here is the one line of attribution around
# it, which is the opposite of a leak: it says whose words these are.
#
# ⚠ AND WHY IT CANNOT SIMPLY BE A TOKEN. `preset_library.top_up` is ADDITIVE — a preset already in
# `_global/asks/` is never overwritten, because its content belongs to the admin. So an instance
# whose `extend.md` predates `{{instruction}}` would substitute nothing, and the one thing the
# person typed would vanish between their keystroke and the agent, silently. Every deployment that
# has ever run this feature is in exactly that state. So the token is the good path and this is the
# floor: when the preset does not ask for the line, the line is appended, attributed, at the end.

#: How the line is introduced to the agent. ONE spelling, shared with the client's fallback sentence
#: (`minutes/extend.ts` — `INSTRUCTION_LEAD`) and with the two asks that carry the token, so the
#: agent reads the same sentence whichever path the words took.
INSTRUCTION_LEAD = "They typed this on the button, in their own words — what to do with it:"

#: The token an ask uses to place the line itself.
INSTRUCTION_TOKEN = "{{instruction}}"


def instruction_of(intent) -> str:
    """The person's line, flattened to one and stripped, or ``""``. Never None, never the word."""
    d = intent if isinstance(intent, dict) else {}
    return " ".join(str(d.get("instruction") or "").split())


def with_instruction(text: str, ask: str, intent) -> str:
    """`text` (the substituted preset) with the person's line guaranteed to be in it.

    The check is on the RAW `ask`, never on the substituted output: searching the output for the
    line itself would false-positive the moment somebody types a word the preset already uses
    ("links", "page"), and the failure would be the silent one — their sentence dropped because the
    preset happened to contain it."""
    line = instruction_of(intent)
    if not line or INSTRUCTION_TOKEN in (ask or ""):
        return text
    return f"{text}\n\n{INSTRUCTION_LEAD}\n\n{line}"
