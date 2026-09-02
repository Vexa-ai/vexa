"""PANEL — the links that compose what a person SEES.

``deeplink`` mints them: a fresh chat already holding an admin-written preset, one meeting's tab, a
file beside a transcript, the lifecycle shapes. It is the one tool here that reaches no service —
it composes a URL out of the terminal's base and a validated preset NAME, and the preset's words
live in ``_global/asks/<name>.md`` where only an admin can write them. That is the whole security of
an ask link, and it is why nothing free-form is allowed into one.

NAVIGATE / PIN / STATE ARE NOT HERE. Moving the person's panel from a tool needs routes that do not
exist on this line — agent-api serves no panel surface today. When they exist this is where they
land; until then their absence is the honest state, not a stub.

ONE KNOWN DUPLICATE, LEFT DELIBERATELY: ``flows_steps/common.py:ui_link`` mints the same grammar
(seam inventory B5, row 3). Unifying them needs a module both images can import, which does not
exist — filed, not faked.
"""
from __future__ import annotations

import json
import re

from .. import config
from ..identity import CALL_TOKEN, anon_guard, caller_email, me
from ..shaping import meeting_ref, ui_meeting_url, ws_url
from ..registry import tool

# A preset NAME and only a name — the narrow, lowercase reading of the same test the terminal
# applies before it will resolve one, so everything mintable here is openable there. The preset
# BODY lives in the admin-written _global/asks/<name>.md and never in the URL: a link that could
# carry prompt text would let anyone who can send a link drive the recipient's agent.
_ASK_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# A meeting ref on an ask link is substituted into the preset's {{meeting}}, so it lands INSIDE
# the prompt the reader's agent opens holding. Anything free-form there is prompt text through a
# second door, which is exactly what the name rule above exists to shut. Two shapes only.
_ASK_MEETING = re.compile(r"^(?:\d{1,12}|[a-z][a-z0-9_-]{0,31}/[A-Za-z0-9._-]{1,128})$")


@tool
@anon_guard
def deeplink(target: str, ref: str = "", name: str = "", meeting: str = "", ws: str = "",
             token: str = "") -> str:
    """A link that opens the Vexa terminal in a specific state — hand it to your person
    whenever you talk about a thing they might want to SEE.

    WHICH LINK TO HAND
    - they should DO something in a fresh chat — review the minutes, prep the call, answer a
      standing question → target='ask', name=<preset>, optionally meeting=<row id> and
      ws=<workspace slug>. You choose WHICH preset; you never choose what it says.
    - they should LOOK at one meeting → target='meeting', ref=<row id | link | platform/native>.
    - they should see two things at once → 'view', or 'pre_meeting'/'during_meeting'/
      'post_meeting' for the lifecycle shapes.
    - they should read one file → target='workspace_file', ref=<path>.
    - the whole list → 'meetings'. The org-level setup conversation → 'setup_global'.

    target: 'ask' (name = a preset in _global/asks/; meeting and ws are refs the preset may
    substitute), 'meeting' (ref = a meeting link or platform/native), 'meetings' (the list),
    'workspace_file' (ref = path), 'setup_global' (the org-level setup conversation),
    'view' (ref = pane spec 'file:<path>,meeting:<platform/native>,readme' — first pane
    left, the rest split beside it: YOU compose what the person sees), or the lifecycle
    presets 'pre_meeting' / 'during_meeting' / 'post_meeting' (ref = platform/native,
    optionally 'platform/native|<doc path>' to put a specific file beside the meeting).

    NEVER put prompt text in a link. name= is a preset NAME; the words behind it are a file
    only an admin can write, and that is the whole security of the ask link."""
    me()
    import urllib.parse as _up
    em = caller_email()
    as_q = f"as={_up.quote(em)}" if em else ""
    if target == "ask":
        nm = name.strip()
        if not _ASK_NAME.match(nm):
            return json.dumps({"error": "ask needs name=<preset>: a NAME only, matching "
                                        "^[a-z0-9][a-z0-9_-]{0,63}$. The preset's words live in "
                                        "_global/asks/<name>.md, which only an admin can write — "
                                        "a link never carries prompt text."})
        q = {"ask": nm}
        w = ws.strip()
        if w:
            if not _ASK_NAME.match(w):
                return json.dumps({"error": "ws must be a workspace slug matching "
                                            "^[a-z0-9][a-z0-9_-]{0,63}$"})
            q["ws"] = w
        mr = meeting.strip()
        if mr:
            if not _ASK_MEETING.match(mr):
                return json.dumps({"error": "meeting must be a row id (digits) or platform/native — "
                                            "it is substituted into the preset's {{meeting}}, "
                                            "so free text there is prompt text by another door."})
            q["meeting"] = mr
        return json.dumps({
            "url": f"{config.UI_BASE}/?{_up.urlencode(q)}",
            "opens": "a fresh chat already holding that preset, over the workspaces the preset "
                     "names — context and opening prompt arrive together",
            "the_words_are_not_in_the_link": f"they are in _global/asks/{nm}.md; editing that "
                                             f"file changes every future click, and nothing is "
                                             f"rebuilt",
        })
    if target == "meeting":
        if ref.strip().isdigit():
            return json.dumps({"url": ui_meeting_url("", "", row_id=ref.strip()),
                               "opens": "the terminal with this exact meeting's tab active"})
        platform, mid = meeting_ref(ref) if "://" in ref else (
            tuple(ref.split("/", 1)) if "/" in ref else (None, "give platform/native or a link"))
        if not platform:
            return json.dumps({"error": mid})
        return json.dumps({"url": ui_meeting_url(platform, mid),
                           "opens": "the terminal with this meeting's tab active — recap, "
                                    "transcript, share"})
    if target == "meetings":
        return json.dumps({"url": f"{config.UI_BASE}/?{as_q}" if as_q else config.UI_BASE,
                           "opens": "the terminal on their meetings list"})
    if target == "workspace_file":
        return json.dumps({"url": ws_url(ref, token or CALL_TOKEN.get() or ""),
                           "opens": "the file, rendered"})
    if target in ("view", "pre_meeting", "during_meeting", "post_meeting"):
        # Composed layouts: the existing shell, filled deliberately. 'view' takes a raw pane
        # spec (file:<path>,meeting:<platform/native>,readme — first pane left, the rest
        # split beside). The named lifecycle presets expand HERE, server-side, so the
        # combinations evolve without touching the terminal.
        doc = ""
        mref = ref
        if "|" in ref:
            mref, doc = ref.split("|", 1)
        if target == "view":
            spec = ref
        else:
            context = f"file:{doc}" if doc else "readme"
            spec = f"{context},meeting:{mref.strip()}"
        q2 = {"view": spec}
        if em:
            q2["as"] = em
        return json.dumps({
            "url": f"{config.UI_BASE}/?{_up.urlencode(q2)}",
            "opens": ("the terminal with exactly the panes listed" if target == "view" else
                      "the terminal composed: context pane left, the meeting beside it"),
        })
    if target == "setup_global":
        q = f"?setup=global" + (f"&{as_q}" if as_q else "")
        return json.dumps({"url": f"{config.UI_BASE}/{q}",
                           "opens": "the org-level setup conversation"})
    return json.dumps({"error": "target must be ask | meeting | meetings | workspace_file | view | pre_meeting | during_meeting | post_meeting | "
                                "setup_global"})
