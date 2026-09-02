"""Shared plumbing for production steps: config by env (P14), tiny HTTP, admin/user auth.
STATELESS BY LAW: everything a step needs travels in ctx.refs / ctx.prior — worker restarts
must be invisible (the duplicate-email lesson)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from typing import Optional

GATEWAY = os.environ.get("VEXA_FLOWS_GATEWAY_URL", "http://localhost:18056")
AGENT_API = os.environ.get("VEXA_FLOWS_AGENT_API_URL", "http://localhost:18100")
ADMIN_API = os.environ.get("VEXA_FLOWS_ADMIN_API_URL", "http://localhost:18057")
ADMIN_KEY = os.environ.get("VEXA_FLOWS_ADMIN_KEY", "changeme")
FIXTURE_TRANSCRIPT = os.environ.get("VEXA_FLOWS_FIXTURE_TRANSCRIPT", "") == "1"   # declared double


#: The ONE name for the internal-tier secret — the compose/helm secret key, the name admin-api,
#: gateway, meeting-api and agent-api read. Flows had a THIRD spelling of its own, which is exactly
#: how the published literal `vexa-internal-secret` came to sit on this file's refusal list and on
#: nobody else's (F95): one secret with three names has three refusal lists and they drift.
INTERNAL_SECRET_ENV = "INTERNAL_API_SECRET"
#: Read when the canonical name is absent, with a deprecation warning. Removed next release.
INTERNAL_SECRET_ENV_DEPRECATED = ("VEXA_INTERNAL_SECRET", "VEXA_INTERNAL_API_SECRET")
#: Placeholder literals a stock deploy surface once supplied. Every one of these is PUBLISHED in the
#: OSS repository, so a deployment holding one is not configured — it is open, wearing a configured
#: face. Same list as the services' config.v1 `forbidden_values`.
INTERNAL_SECRET_PLACEHOLDERS = ("vexa-internal-secret", "lite-internal-secret", "changeme",
                                "change-me", "CHANGE-ME", "default", "secret")


def require_internal_secret() -> str:
    """The INTERNAL-TIER secret, or the process refuses to start.

    agent-api's meeting room (`control_plane/meeting_room.py`, gate 0) opens only for a caller that
    presents `X-Internal-Secret`. A browser client through the gateway holds no such secret and
    therefore cannot open a room at all — which is the entire point of the gate, and why flows,
    which is an internal-tier caller, must hold one.

    Deliberately the SAME REFUSAL as `flows_api._require_api_key`, down to the wording: a weak
    default makes an unconfigured deployment look configured and fails no test, so there is no
    default and no fallback. The value lives in a mode-600 file under `~/.storm/`, exported by the
    lane's start script; it never appears in this repository, in a log, or in an error message —
    including the ones below, which name the VARIABLE and never the value.

    TWO things changed with F95. The variable is now `INTERNAL_API_SECRET`, the one name every other
    service uses (the old spellings still work for one release and say so); and the placeholder list
    carries the literals the deploy surfaces actually shipped, not four generic ones — the refusal
    that missed `vexa-internal-secret` was a refusal list written from imagination rather than from
    the compose file it was defending against.
    """
    key = (os.environ.get(INTERNAL_SECRET_ENV) or "").strip()
    if not key:
        for legacy in INTERNAL_SECRET_ENV_DEPRECATED:
            key = (os.environ.get(legacy) or "").strip()
            if key:
                print(f"WARNING: {legacy} is DEPRECATED — rename it to {INTERNAL_SECRET_ENV}, the "
                      f"one name the whole internal tier uses. Honoured this release, removed next.",
                      file=sys.stderr)
                break
    if not key:
        raise RuntimeError(
            f"{INTERNAL_SECRET_ENV} is unset — flows refuses to start rather than run with no "
            "internal-tier identity. Mint one into a mode-600 file (the ~/.storm/dburl pattern) "
            "and export it from the lane's start script; never put the value in the repo.")
    if key in INTERNAL_SECRET_PLACEHOLDERS:
        raise RuntimeError(
            f"{INTERNAL_SECRET_ENV} is the placeholder {key!r} — refusing to start. That literal is "
            "published in this repository, so it authenticates nobody and everybody.")
    return key

# Where a person's own terminal lives. Same env name the control MCP already reads, and the same
# default — one deployment fact, one variable, never two spellings of one host. A mail that says
# "open it here" and names a host the person cannot reach is worse than a mail with no link.
UI_URL = os.environ.get("VEXA_UI_URL", "http://localhost:18300").rstrip("/")


def ui_link(**params) -> str:
    """A composed terminal deeplink: ``ui_link(ask="minutes-review", meeting=41)``.

    NO PRODUCTION TOUCH IS BUILT THIS WAY ANY MORE — every step that creates one mints a SCAFFOLD
    (`mint_scaffold`, below), because a url carrying a preset name leaves the UI and the agent to
    compose their own halves and they disagree. This remains the HAND-LINK builder: `?ask=` is
    still a real entry point in the terminal (it mints a local scaffold client-side), and the eval
    harnesses address it directly.

    The params compose (``?ask=`` primes a chat, ``?meeting=`` opens the room) and they survive
    the sign-in hop, so ONE url is both the door and the destination. Empty values are dropped —
    a link with ``?meeting=`` and nothing after it reads as a bug to the person who hovers it.
    """
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in params.items() if v not in (None, "", [])})
    return f"{UI_URL}/?{q}" if q else f"{UI_URL}/"


def mint_scaffold(kind: str, recipient: str, *, opening: str,
                  meeting_id=None, refs: Optional[dict] = None,
                  workspaces: Optional[list] = None,
                  tabs: Optional[list] = None, focus: Optional[str] = None,
                  share_token: Optional[str] = None,
                  provenance: Optional[dict] = None) -> str:
    """THE LINK, minted as a SCAFFOLD (PRD §5.5). Returns the url, or RAISES.

    This replaces `ui_link` everywhere a step creates a TOUCH. The difference is not cosmetic and
    it is the whole point of the primitive: `ui_link` built a url out of a preset name and a
    meeting ref and left both renderers behind it — the terminal panel and the agent's first turn —
    to compose the rest out of whatever they could find. They disagreed, in every way the alpha
    ledger records: the chat opened on a Zoom number (F1), the panel opened the reader's own README
    (F19), the phase greeting beat the preset (F5), the header said UPCOMING about a meeting that
    had happened (F4). A scaffold is ONE record both of them read.

    A FAILED MINT RAISES, and the caller must not send. That is the share-gate doctrine one layer
    up, in the same words `email_attendees` already uses for a share it could not mint: *a mail
    whose only button opens a chat that cannot see the meeting is worse than no mail*. Everything
    that can be wrong is checked at the mint — the preset exists, the kind is in the catalogue, the
    terminal has an origin — because the mint is the last moment a step can still choose not to send.

    `share_token` is minted BY THE CALLER (`flows_steps.meeting.mint_transcript_share`) when the
    meeting is not the recipient's own: the restricted grant is written as the meeting's OWNER, and
    the owner's gateway key lives here, not in agent-api. agent-api composes the token into the url
    so the link is still built in exactly one place.
    """
    payload = {"who": recipient, "kind": kind, "opening": opening}
    if meeting_id not in (None, ""):
        payload["meeting"] = str(meeting_id)
    for key, value in (("refs", refs), ("workspaces", workspaces), ("tabs", tabs),
                       ("focus", focus), ("share_token", share_token), ("provenance", provenance)):
        if value not in (None, "", [], {}):
            payload[key] = value
    code, body = http("POST", f"{AGENT_API}/internal/scaffolds",
                      {"X-Internal-Secret": require_internal_secret()}, payload)
    url = body.get("url") if isinstance(body, dict) else None
    if 200 <= int(code or 0) < 300 and url:
        return str(url)
    from flows import StepError
    detail = (body.get("detail") if isinstance(body, dict) else str(body))
    # 5xx is the platform having a moment; a 4xx is a fact about this preset, this kind or this
    # deployment and retrying it only delays the mail without changing the answer. Same split as
    # `mint_transcript_share`, deliberately — one rule for one class of failure.
    raise StepError(
        f"no scaffold could be minted for {recipient} ({kind}/{opening}"
        + (f", meeting {meeting_id}" if meeting_id else "")
        + f"): HTTP {code} — {str(detail)[:200]}. Not sending: a link that opens onto nothing is "
          "worse than no mail.",
        retryable=int(code or 0) >= 500 or int(code or 0) == 429)


def db_url() -> str:
    url = os.environ.get("VEXA_FLOWS_DB_URL")
    if url:
        return url
    pw = subprocess.run(["docker", "exec", "vexa-v012-postgres-1", "sh", "-c", "echo -n $POSTGRES_PASSWORD"],
                        capture_output=True, text=True).stdout.strip()
    return f"postgresql+psycopg://postgres:{pw}@127.0.0.1:5458/flows"


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 20):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in {"content-type": "application/json", **headers}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except Exception as e:  # noqa: BLE001 — steps turn this into a typed retry
        from flows import StepError
        # The reason column is the only thing anyone reads when a reaction is stuck, so it has to
        # carry the cause. It used to carry `TypeError` alone — the class name of an exception
        # raised while building a header, against a url this line had already truncated to the
        # host. Hours went into finding a deleted user behind that word.
        raise StepError(f"http {method} {url}: {type(e).__name__}: {e}"[:400])


def platform_user_id(email: str) -> str:
    """This person's platform id IF THEY ALREADY HAVE ONE, else "". NEVER creates.

    `ensure_platform_user` was once the only door, so a step that merely wanted to know whether
    somebody is already a user had to mint an account to find out — and an account minted by a mail
    nobody asked for is a ghost that later reads as an adopted user. Asking is a different verb
    from creating, and this is the asking half: one GET, no side effect, "" for a stranger.
    """
    code, u = http("GET", f"{ADMIN_API}/admin/users/email/{email}", {"X-Admin-API-Key": ADMIN_KEY})
    return str(u["id"]) if code == 200 and isinstance(u, dict) and u.get("id") is not None else ""


def ensure_platform_user(email: str) -> str:
    """This person's platform id, CREATING the account when they have none.

    The creating half, written ON TOP OF the asking half rather than beside it: the same lookup
    used to be spelled out twice, so a change to how we ask would have had to be made in two places
    and would have been made in one."""
    existing = platform_user_id(email)
    if existing:
        return existing
    _code, u = http("POST", f"{ADMIN_API}/admin/users", {"X-Admin-API-Key": ADMIN_KEY},
                    {"email": email, "name": email.split("@")[0].title()})
    return str(u["id"])


def user_api_key(uid: str) -> str:
    """This user's gateway key, or a StepError that says why there isn't one.

    Returning None here was the whole bug: the caller put it straight into an X-API-Key header,
    urllib died joining it, and the reaction blamed the gateway for a 404 from the admin API. A
    key that cannot be minted is a fact about the account, and it belongs in the reason.
    """
    st, tok = http("POST", f"{ADMIN_API}/admin/users/{uid}/tokens",
                   {"X-Admin-API-Key": ADMIN_KEY}, {"scopes": ["bot", "browser", "tx"]})
    key = tok.get("token") or tok.get("key") if isinstance(tok, dict) else None
    if not key:
        from flows import StepError
        detail = (tok.get("detail") if isinstance(tok, dict) else str(tok))
        raise StepError(f"no api key for platform user {uid} — admin api said {st}: "
                        f"{str(detail)[:120]}")
    return key


def ws_file(uid: str, path: str, slug: Optional[str] = None) -> Optional[str]:
    q = f"&slug={slug}" if slug else ""
    code, body = http("GET", f"{AGENT_API}/api/workspace/file?path={path}{q}", {"X-User-Id": uid})
    return body.get("content") if code == 200 and isinstance(body, dict) else None


# A person's preferences, read from their workspace. The MCP writes this file; steps read it
# here. Defaults are the floor for someone who has never set anything — the MCP's vocabulary is
# the source of truth, and it materialises every key the first time a setting is touched.
_SETTING_DEFAULTS = {
    "bot_name": "Vexa",
    "mail_minutes": True, "mail_join": False, "mail_rsvp": True, "timezone": "",
    # the prepare-for-this-meeting note, the twin of mail_minutes at the other end of the meeting.
    # ON like its twin — a loop that ships OFF is a loop nobody sees. NOTE: the control MCP owns
    # the SETTINGS_VOCAB a person actually edits, and it has no `mail_prep` entry yet, so today
    # this default IS the switch. That entry is the one thing needed to make it turn-off-able.
    "mail_prep": True,
}


def setting(uid: str, key: str):
    """One preference for one person. Never raises: a missing file means defaults."""
    import json as _j
    try:
        raw = _j.loads(ws_file(uid, ".settings.json") or "{}")
    except Exception:  # noqa: BLE001
        raw = {}
    return raw.get(key, _SETTING_DEFAULTS.get(key))


def scaffolded(uid: str, slug: Optional[str] = None) -> bool:
    return ws_file(uid, ".scaffolded", slug) is not None
