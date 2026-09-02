"""mailtext.py — the WORDS this deployment mails, as files a human edits, not strings in a step.

Founder, 2026-09-02, on the mails a stranger sees first: the wording is his and he will rewrite it.
That sentence is a requirement about the SOFTWARE, not about the copy: if his rewrite means
touching a Python string, it means a review, a rebuild and a deploy, and it will not happen at the
speed a first impression needs to be fixed at. So every mail body this deployment sends lives in a
file, and a rewrite is a file edit.

The shape is the one the PRD already names (§3.1/§3.2, "baked default, data override") and that the
deeplink presets already ship in:

    baked default (this file)  ->  `_global/mail/<name>.md` overrides it  ->  read hot, per send

`_global` is git-backed, admin-only-writable and mounted into every worker, so an override is a
reviewable commit rather than a silent mutation, and nothing is rebuilt when it changes.

TWO HALVES OF THE INTRODUCTION, AND ONLY ONE IS EDITABLE. The company half — who this Vexa belongs
to — is read from `_global/README.md`, which the admin wrote during instance setup. The service
half is FIXED PRODUCT TEXT: what Vexa does is not a per-deployment opinion, and a company that
edits it into something Vexa does not do has written a promise the product will break.
"""
from __future__ import annotations

import re
from typing import Optional

from .common import ws_file

# The fixed half. Not admin-editable, deliberately. The same sentence is in the MCP instructions
# so the chat and the mail introduce the product identically -- a person who reads one and then
# the other must not meet two different products.
SERVICE_SENTENCE = ("I sit in meetings you are invited to; afterwards you get what came out of "
                    "them and what they leave on your plate.")

# THE USER-FACING NAME of a person's own workspace. Founder, 2026-09-02: it is a **DESK** — a
# personal desk, and a group desk for a group. Not a placeholder any more; the word is chosen.
# Change it HERE and every mail this runtime sends follows. Code paths, slugs and API fields keep
# saying "workspace" deliberately — renaming those is a migration, and a migration is not what a
# naming decision should cost.
#
# The word carries the meaning the founder attached to it, and it is the reason "private" was the
# wrong word: a desk is COMPANY KNOWLEDGE HELD BY ONE PERSON. The company's agents may read it for a
# meeting that person is in. `_system` — chats, sessions, settings — stays private and is not a desk.
#
# The terminal has the same constant on its side (clients/terminal/src/minutes/vocabulary.ts) and
# fills `{{workspace}}` in the presets from it; the two runtimes cannot share a literal, so they
# name each other. Those two lines are the whole rename.
#
# Note what does NOT use it: the visibility sentence below says "workspaces", the ordinary English
# word, not the product's name for one — it is describing where things are kept, not naming a
# surface, and a stranger reading their first mail should not have to learn a product noun to
# understand who can see their notes.
WORKSPACE_WORD = "desk"

# WHO CAN SEE WHAT, in the founder's own words. It goes into the mails a person reads before they
# have decided whether to keep anything here, because that is the only moment at which telling them
# is a choice they still have. Not a disclaimer and not a legal line: three facts.
VISIBILITY_SENTENCE = ("Vexa runs on this organisation's own servers; what you and your colleagues "
                       "keep in your workspaces is visible to the company's agents; recordings and "
                       "transcripts stay here.")

# The company half's fallback. If this string ever reaches a recipient it is a BUG in the gate --
# no mail should send at all while the company layer is missing -- so it is written to be
# recognisable in an inbox rather than to read smoothly.
COMPANY_UNSET = "this company (setup incomplete)"

_H1 = re.compile(r"^#\s+(.+?)\s*$")

# The baked defaults. PLACEHOLDER WORDING throughout: the founder has not chosen these words yet,
# and every one of them is his to rewrite by editing the matching file in `_global/mail/`. They say
# the substance plainly and do not embellish it.
DEFAULTS: dict[str, str] = {
    # The prepare note. Goes to the ORGANISER and to people who are already users -- never to a
    # stranger. A 50-person meeting must not produce 50 mails to people who have never heard of us,
    # and a mail before the meeting has nothing yet to justify itself with.
    # It is a TEMPLATE: substitutions only, no agent turn, and it must NOT claim a workspace was
    # started for anyone -- nothing is built for a person who has not clicked.
    "prepare": (
        "subject: Prepare: {{title}}\n"
        "---\n"
        "{{title}} — {{when}}.\n"
        "Want to walk in ready? Open the chat and I will pull together what we already know."
    ),
    # The ATTENDEE follow-up head -- for most people in a large meeting this is the first time they
    # hear from Vexa at all, so it is the whole introduction: whose Vexa this is, what it does,
    # which meeting, who had it in the room, where the report now lives, and who can see it.
    #
    # BYTE-FOR-BYTE `deploy/dogfood/mail/attendee-head.md`, and it has to stay that way: the README
    # in that directory says the source and the baked default are the same content or the source
    # lies. It HAS drifted twice already, both times invisibly, because nothing read both -- once
    # substituting {{title}}/{{when}}/{{attendees}} which no step fills, and once missing the
    # visibility sentence entirely. `test_the_baked_defaults_match_the_files_in_deploy_dogfood_mail`
    # now reads both, for every template.
    #
    # THREE SENTENCES ARE LITERAL HERE, not tokens: the service sentence, "your desk", and the
    # visibility sentence. `render` would fill {{service}}, {{workspace}} and {{visibility}}, but
    # the FILE spells them out and these two strings must be equal. The visibility one especially
    # must not become a token that a deployment could leave unfilled: it is the sentence that tells
    # a stranger who can see their notes, in the first mail they ever get from us, and a baked
    # fallback that quietly drops it discloses nothing. Change any of the three and you change
    # every place the mail README names.
    "attendee-head": (
        "subject: {{meeting}} — what it means for you\n"
        "---\n"
        "I am Vexa, the meeting assistant at {{company}}. I sit in meetings you are invited to; "
        "afterwards you get what came out of them and what they leave on your plate.\n"
        "\n"
        "{{organizer}} had me in {{meeting}} on {{date}}. This is now on your desk.\n"
        "\n"
        "Vexa runs on this organisation's own servers; what you and your colleagues keep in your "
        "workspaces is visible to the company's agents; recordings and transcripts stay here.\n"
    ),
    # The MINUTES head -- the same meeting, to someone who already knows what Vexa is. No
    # introduction: repeating it to a returning person is the tell of a machine that does not know
    # who it is talking to.
    "minutes-head": (
        "subject: Minutes: {{title}}\n"
        "---\n"
        "{{title}} — {{when}}. This is now on your {{workspace}}.\n"
        "\n"
        "{{visibility}}\n"
    ),
}


def mailbox_address() -> str:
    """THE address a person adds to their meetings, from the deployment that actually watches it.

    A DEPLOYMENT FACT, never a guess and never the address a mail was sent FROM — on this stack
    those differ, and the send-from box is not watched. It is read from the same `VEXA_MAIL_ADDR`
    the inbound poller answers as, so the sentence we tell people to use and the mailbox we read
    cannot drift apart: there is one value and both sides take it from there."""
    import os
    return (os.environ.get("VEXA_MAIL_ADDR") or "").strip() or "the Vexa mailbox for this deployment"


def company_name(uid: str) -> str:
    """WHO THIS VEXA BELONGS TO, read from the company layer the admin wrote.

    The first heading of `_global/README.md`, which the setup verb refuses to accept without.
    Read per send rather than cached: an admin who corrects the company name expects the next mail
    to carry the correction, and mail volume is nowhere near a rate at which this read matters."""
    readme = ws_file(uid, "README.md", "_global") or ""
    for line in readme.splitlines():
        if not line.strip():
            continue
        m = _H1.match(line)
        return m.group(1).strip() if m else COMPANY_UNSET
    return COMPANY_UNSET


def _split(raw: str) -> tuple[str, str]:
    """`subject: …` on the first line, `---`, then the body. Anything else is all body with no
    subject, which the caller then has to supply -- a template that silently lost its subject line
    would mail an empty one."""
    lines = raw.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip()
        rest = lines[1:]
        if rest and rest[0].strip() == "---":
            rest = rest[1:]
        return subject, "\n".join(rest).strip()
    return "", raw.strip()


def render(name: str, uid: str, values: Optional[dict] = None) -> tuple[str, str]:
    """(subject, body) for one mail. `_global/mail/<name>.md` if the admin wrote one, else baked.

    `{{company}}` and `{{service}}` are filled here so no caller can forget them or spell the
    product differently; everything else comes from `values`. An unknown `{{token}}` is left
    STANDING rather than blanked: a visible `{{organizer}}` in a test inbox is a bug report, and a
    silently empty sentence is not."""
    raw = ws_file(uid, f"mail/{name}.md", "_global")
    # An override that is EMPTY, or only whitespace, is an accident and not an instruction. `or`
    # alone did not catch it -- `"   \n\n"` is truthy in Python, so an admin who cleared the file
    # instead of editing it would have mailed a stranger a blank introduction with a blank subject,
    # which is worse than the placeholder wording they were trying to replace.
    if not (raw or "").strip():
        raw = DEFAULTS.get(name)
    if raw is None:
        raise KeyError(f"no mail template named {name!r} (baked or in _global/mail/)")
    subject, body = _split(raw)
    fill = {"company": company_name(uid), "service": SERVICE_SENTENCE,
            "visibility": VISIBILITY_SENTENCE, "workspace": WORKSPACE_WORD,
            "mailbox": mailbox_address(), **(values or {})}
    for key, val in fill.items():
        token = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
        subject = token.sub(str(val), subject)
        body = token.sub(str(val), body)
    return subject, body
