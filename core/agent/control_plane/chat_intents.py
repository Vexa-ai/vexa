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
from shared.marks import MACHINERY_MARK, PHASE_MARK, job_mark  # noqa: E402 — re-export, see above
SILENT_PREFIX = MACHINERY_MARK + " " + PHASE_MARK + " "


# Kinds whose act must NOT hold the chat (Vexa-ai/vexa#1584). Create and Extend read, search and
# write for 30-120s; the founder pressed them four times on 2026-09-06 and the composer was busy
# throughout. They run as background JOBS: the turn returns one line at once, the job runs on its
# own thread in the worker, and its result lands as a line plus a refreshed page tab.
#
# A CLOSED SET, like SILENT_KINDS above and for the same reason: whether an act may spawn a job is
# not a flag the wire gets to set. `explore` and `highlight` stay inline — one is a chip lookup, the
# other is machinery that publishes terms and says nothing.
JOB_KINDS = frozenset({"create", "extend"})


def is_job(intent) -> bool:
    """Does this intent run as a background job? Reads the KIND, never a client-supplied flag."""
    if not isinstance(intent, dict):
        return False
    return str(intent.get("kind") or "").strip().lower() in JOB_KINDS


def job_target(intent) -> str:
    """The ONE thing the job acts on — the workspace-qualified page path. The duplicate refusal keys
    on this string, so it has to name the page the same way twice: a bare path in one workspace and
    the same path in another must not collide, and the same page reached twice must."""
    d = intent if isinstance(intent, dict) else {}
    ws = str(d.get("workspace") or "").strip()
    path = str(d.get("path") or "").strip()
    return f"{ws}/{path}" if ws and path else (path or ws)


def job_prefix(intent) -> str:
    """The mark a job act carries, or ``""``. The route prefixes it exactly as it prefixes
    SILENT_PREFIX — one line there, the closed set and the composition here, where they are tested."""
    if not is_job(intent):
        return ""
    return job_mark(str(intent.get("kind") or "").strip().lower(), job_target(intent))


def is_silent(intent) -> bool:
    """Is this turn one the person must never see as a bubble?

    Reads the KIND, not a client-supplied flag. `intent.silent: true` on the wire would let anyone
    able to mint an intent make a turn invisible in someone else's conversation, which is the same
    capability `opening` is a NAME rather than a string to deny. The closed set is here."""
    if not isinstance(intent, dict):
        return False
    return str(intent.get("kind") or "").strip().lower() in SILENT_KINDS


def preset_for(intent) -> str | None:
    """The preset name this intent runs, or None when there is nothing safe to run."""
    if not isinstance(intent, dict):
        return None
    return INTENT_PRESETS.get(str(intent.get("kind") or "").strip().lower())


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
        "term": s("term"),
        "meeting": s("meeting"),
        "segment": s("segment"),
        "since": s("since"),
    }
