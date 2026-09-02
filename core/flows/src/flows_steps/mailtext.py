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
    # which meeting, and who had it in the room. The agent's own per-person section follows it.
    "attendee-head": (
        "subject: {{title}} — what it means for you\n"
        "---\n"
        "I am Vexa, the meeting assistant at {{company}}. {{service}}\n"
        "\n"
        "{{organizer}} had me in {{title}}, {{when}}, with {{attendees}} others in the room.\n"
    ),
    # The MINUTES head -- the same meeting, to someone who already knows what Vexa is. No
    # introduction: repeating it to a returning person is the tell of a machine that does not know
    # who it is talking to.
    "minutes-head": (
        "subject: Minutes: {{title}}\n"
        "---\n"
        "{{title}} — {{when}}.\n"
    ),
}


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
    raw = ws_file(uid, f"mail/{name}.md", "_global") or DEFAULTS.get(name)
    if raw is None:
        raise KeyError(f"no mail template named {name!r} (baked or in _global/mail/)")
    subject, body = _split(raw)
    fill = {"company": company_name(uid), "service": SERVICE_SENTENCE, **(values or {})}
    for key, val in fill.items():
        token = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}")
        subject = token.sub(str(val), subject)
        body = token.sub(str(val), body)
    return subject, body
