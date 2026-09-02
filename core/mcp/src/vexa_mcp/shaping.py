"""Shaping a service's answer into a tool's answer.

Everything here is about the RESPONSE — budgets, links, refusal wording, the two-value resolution
of a meeting reference. Nothing here reaches a store; the only outbound calls are through
:mod:`vexa_mcp.httpc`, and they are the small resolutions several tools share rather than a
domain of their own.
"""
from __future__ import annotations

import datetime
import json
import re
import urllib.parse
import urllib.request

from . import config, httpc

# The welcome every sign-in response hands the agent, whichever door the person came through.
# Three beats, not five, and only capabilities that work today. Anything listed here is a promise
# made in the first thirty seconds of the relationship, so a beat for a broken path is an invented
# capability with our name on it — however true we mean it to become.
WELCOME_BEATS = [
    "Paste any meeting link and I'll put a notetaker in that call — while it runs, ask me "
    "what's being said.",
    "Afterwards the words stay here: searchable, and written up into notes your team can "
    "read.",
    "Tell me in a sentence what should happen after a meeting — \"after the standup, email "
    "me the open questions\" — and it does.",
]


def capped(obj, limit: int) -> str:
    """Serialise ``obj`` as VALID json inside a response budget of ``limit`` characters.

    The one guarantee is validity, not size: if the budget is too small to hold even the
    "it does not fit" answer, that answer is returned whole rather than cut.

    Every tool used to end ``json.dumps(...)[:N]``, which slices the STRING — so the moment a
    payload outgrew its cap the tool returned a JSON document cut mid-key, and every caller saw a
    parse error or, worse, quietly read nothing. ``meetings_list`` did exactly that: 24 meetings on
    the gateway, exactly 10,000 characters returned, ending ``"start_tim``, and the agent reading it
    concluded the person had NO meetings. A truncation that turns 24 into 0 without an error is not
    a size limit, it is a silent wrong answer.

    So the DATA is trimmed, never the text: the longest list in the payload gives up entries until
    the whole thing fits, and what was dropped is stated in the result where a reader — human or
    agent — will see it.
    """
    out = json.dumps(obj)
    if len(out) <= limit:
        return out
    if isinstance(obj, dict):
        obj = json.loads(out)          # a copy; never mutate the caller's structure
        for _ in range(200):
            holder, key, longest = None, None, 0
            for container in (obj, *(v for v in obj.values() if isinstance(v, dict))):
                for k, v in container.items():
                    if isinstance(v, list) and len(v) > longest:
                        holder, key, longest = container, k, len(v)
            if holder is None or longest == 0:
                break
            total = holder.setdefault("_truncated", {}).get(key, {}).get("total", longest)
            drop = max(1, len(holder[key]) // 8)
            holder[key] = holder[key][:-drop]
            holder["_truncated"][key] = {
                "shown": len(holder[key]), "total": total,
                "note": "trimmed to fit the tool's response budget — ask again with a "
                        "narrower filter, or a limit, to see the rest"}
            out = json.dumps(obj)
            if len(out) <= limit:
                return out
    return json.dumps({"error": "the result does not fit this tool's response budget",
                       "budget_chars": limit,
                       "do": "narrow the request (a filter, a limit, or one id) and ask again"})


def ui_meeting_url(platform: str, native: str, title: str = "", row_id=None,
                   email: str = "") -> str:
    """A link that opens the terminal signed in, with this meeting's tab active. Prefer ``row_id``
    when known: a personal room's native id spans many meetings, and the native resolver picks the
    newest, which may be empty."""
    q = {"meeting": str(row_id) if row_id else f"{platform}/{native}"}
    if email:
        q["as"] = email
    if title:
        q["mtitle"] = title[:80]
    return f"{config.UI_BASE}/?{urllib.parse.urlencode(q)}"


def ws_url(path: str, token: str) -> str:
    """The cloud URL a human can open for a workspace file, viewable with the caller's own
    credential. Handed out alongside every path so the agent never names an unopenable file."""
    base = config.CANONICAL.rsplit("/mcp", 1)[0]
    quoted = urllib.parse.quote(path.strip("/"))
    return f"{base}/w/{quoted}?token={token}" if token else f"{base}/w/{quoted}"


def in_their_clock(epoch: float, tz: str) -> str:
    """A time, always with the zone attached. Never a bare HH:MM."""
    if tz:
        try:
            import zoneinfo
            z = zoneinfo.ZoneInfo(tz)
            t = datetime.datetime.fromtimestamp(epoch, z)
            return t.strftime("%H:%M") + " " + (t.tzname() or tz)
        except Exception:  # noqa: BLE001
            pass
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%H:%M") + " UTC"


CREDENTIAL_REFUSAL = (
    "I will not take a token in chat — anything pasted here stays in this transcript. "
    "Give me the repository URL on its own and I will show you a key to add to it instead."
)


def refuse_credentials(*values) -> str:
    """The refusal, or ``""`` when nothing credential-shaped was passed. Same detector the API uses."""
    tokenish = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
    for v in values:
        text = str(v or "")
        if any(t in text for t in tokenish):
            return CREDENTIAL_REFUSAL
        if "://" in text:
            userinfo = text.split("://", 1)[1].split("/", 1)[0]
            if ":" in userinfo and "@" in userinfo:
                return CREDENTIAL_REFUSAL
    return ""


def deploy_key_state(uid: str, workspace: str, repo: str) -> dict:
    """The ONE next action when a git op is refused for want of a credential: our public key, where
    it goes, and the state the person reports back. Never a place to paste a secret."""
    st, body = httpc.agent("POST", f"/api/workspace/{workspace or 'personal'}/deploy-key", uid,
                           {"repo": repo})
    if st != 200 or not isinstance(body, dict):
        return {"error": "could not prepare a deploy key for this workspace", "status": st}
    return {
        "add_this_key_to_the_repo": body.get("public_key"),
        "add_it_at": body.get("add_at") or "the repository's Settings → Deploy keys",
        "add_it_as": body.get("add_as"),
        "tell_your_person": "Add this key to that repository with WRITE access, then say `done` "
                            "and I will try again. Never paste a token here.",
        "then": "call the same tool again",
    }


def meeting_ref(meeting_url: str):
    """``(platform, native_meeting_id)`` from a pasted link, or ``(None, why-it-failed)``."""
    u = (meeting_url or "").strip()
    m = re.search(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})", u)
    if m:
        return "google_meet", m.group(1)
    m = re.search(r"teams\.live\.com/meet/(\d+)", u)
    if m:
        return "teams", m.group(1)
    m = re.search(r"zoom\.us/j/(\d+)", u)
    if m:
        return "zoom", m.group(1)
    return None, ("could not read that link — send the full meeting URL "
                  "(meet.google.com/xxx-xxxx-xxx, teams.live.com/meet/<id>, zoom.us/j/<id>)")


def resolve_meeting(uid: str, meeting_url: str = "", meeting_id: str = ""):
    """A gateway meeting id from either a pasted link or an explicit id."""
    if meeting_id:
        return str(meeting_id), None
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return None, mid
    st, r = httpc.gw(uid, "GET", "/meetings")
    for m in (r or {}).get("meetings", []) if isinstance(r, dict) else []:
        if m.get("platform") == platform and m.get("native_meeting_id") == mid:
            return str(m.get("id")), None
    return None, "no captured meeting matches that link yet"


# ── the workspace as a JSON store, over the routes agent-api owns ─────────────────────────────
def read_json(uid: str, path: str, default):
    """One JSON file out of the caller's workspace, through ``GET /api/workspace/file``."""
    st, body = httpc.agent("GET", "/api/workspace/file" + httpc.q(path=path), uid)
    if st != 200:
        return default
    try:
        return json.loads((body or {}).get("content") or "")
    except Exception:  # noqa: BLE001
        return default


def write_json(uid: str, path: str, obj) -> bool:
    """One JSON file INTO the caller's workspace, through ``PUT /api/workspace/file``.

    The rig wrote these with ``docker exec -i vexa-dogfood-agent-api-1 sh -c 'mkdir -p … && cat >
    {target}'``, with ``target`` built from a caller-supplied path and left unquoted — a docker
    socket, a hardcoded container name and a shell-injection shape, on the MCP's own write verb
    (seam inventory B6.1, audit V4). The route it called "a missing endpoint" exists: agent-api's
    page editor writes through it, authorises it against the mount rules, and commits it."""
    st, _ = httpc.agent("PUT", "/api/workspace/file", uid, {"path": path, "content":
                                                            json.dumps(obj, indent=1)})
    return 200 <= st < 300


SETUP_SENTENCE = "This Vexa is being set up by its administrator."


def company_layer_state(uid: str) -> dict:
    """What the company layer holds, from the one service that can see the store.

    FAIL-CLOSED like every other reader of this gate: if agent-api cannot answer, the layer is
    missing. A verb that reconfigures the machine must not proceed because a probe timed out."""
    st, body = httpc.agent("GET", "/api/global/state", uid)
    if st != 200 or not isinstance(body, dict):
        return {"global_setup": "missing", "reasons": [f"agent-api answered {st}"],
                "missing_files": [], "you_are_admin": False}
    return body


def refuse_if_gated(verb: str, uid: str):
    """The refusal an operator verb returns while the company layer is missing, or None.

    It NAMES ITSELF. A bare "forbidden" leaves the agent to guess whether it asked wrongly or asked
    too early, and those two have opposite fixes. This is a DIFFERENT refusal from the operator
    gate: that one says "you are not the operator", this one says "there is not yet an organisation
    to operate"."""
    state = company_layer_state(uid)
    if state.get("global_setup") == "completed":
        return None
    return json.dumps({
        "refused": verb,
        "why": f"{verb} is refused: the company layer is not set up. {SETUP_SENTENCE}",
        "missing_files": state.get("missing_files", []),
        "reasons": state.get("reasons", []),
        "next": ("You are the admin — write the five files into _global and call "
                 "mark_global_ready." if state.get("you_are_admin") else
                 "Only the instance admin can lift this."),
    })


# ── the public docs, which need no account ───────────────────────────────────────────────────
_DOCS_CACHE: dict = {}


def docs(url: str) -> str:
    if url not in _DOCS_CACHE:
        try:
            # urllib's default UA is refused at the edge; identify honestly rather than
            # impersonating a browser.
            req = urllib.request.Request(url, headers={
                "User-Agent": "vexa-mcp/0.12 (+https://vexa.ai) python-urllib",
                "Accept": "text/plain, text/markdown, */*",
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                _DOCS_CACHE[url] = r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "code", type(e).__name__)
            return f"(could not reach {url}: {detail})"
    return _DOCS_CACHE[url]


def flows_unavailable(tool: str, detail: str = "") -> str:
    """One tool, named, is off — and the server is fine. An agent has to be able to tell those
    apart: a traceback out of an import reads as "Vexa is broken" when the truth is "this
    deployment does not carry that piece"."""
    return json.dumps({
        "unavailable": tool,
        "reason": detail or "this deployment does not carry that surface",
        "scope": "this tool only — every other tool on this server is unaffected",
    })
