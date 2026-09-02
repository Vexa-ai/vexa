"""WHO IS CALLING — resolved in exactly one place, for every tool.

Three credentials reach this server and all three end here:

  * a durable ``vxa_mcp_…`` token (the sign-in flow's output, or ``VEXA_TOKEN`` on a stdio install);
  * a ``vxd_…`` DELEGATION token, minted per dispatch by agent-api for the worker it spawns —
    verified by :mod:`vexa_mcp.delegation`, which is ``core/agent/shared/delegation.py``
    VERBATIM (``tests/test_delegation_mirror.py`` asserts byte-identity). The rig hand-rolled a
    second HMAC verifier with its own prefix, audience and denylist; two verifiers on one security
    surface with no test comparing them is how the weaker one survives (seam inventory B5, row 1);
  * the registration URL's one-time code, which the web layer turns into a subject before the tool
    runs.

Everything else in the package asks :func:`me`.
"""
from __future__ import annotations

import contextvars
import functools
import json
import time

from . import config, delegation, httpc

# Set by the web layer (bearer header, session bind) or, when RIG_MODE is on, by a ``token=`` call
# argument. Never threaded through a signature: identity is decided once and read where a verb
# needs it.
CURRENT = contextvars.ContextVar("vexa_subject", default=None)
CALL_TOKEN = contextvars.ContextVar("vexa_call_token", default=None)
CALL_SCOPE = contextvars.ContextVar("vexa_call_scope", default=None)
GHOST_UID = contextvars.ContextVar("vexa_ghost_uid", default=None)
SESSION_BIND: dict = {}


# ── the durable token store ──────────────────────────────────────────────────────────────────
def tokens() -> dict:
    try:
        return json.loads(config.TOKENS_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def mint_token(uid: str, email: str) -> str:
    """A durable ``vxa_mcp_…`` token for a person who just proved they read that mailbox."""
    import secrets
    tok = "vxa_mcp_" + secrets.token_urlsafe(24)
    d = tokens()
    d[tok] = {"uid": str(uid), "email": email}
    config.TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TOKENS_FILE.write_text(json.dumps(d, indent=1))
    return tok


def revoked_jtis() -> set:
    """The denylist — token ids struck off before their exp. Read per call so revoking is immediate;
    a missing or unparseable file means NOTHING is revoked, which is the correct default: the
    file's absence must not lock everyone out."""
    try:
        data = json.loads(config.REVOKED_FILE.read_text())
    except Exception:  # noqa: BLE001
        return set()
    if isinstance(data, dict):
        data = data.get("revoked", [])
    return {str(x) for x in data} if isinstance(data, list) else set()


def verify_delegation(tok: str) -> dict:
    """The library's verifier, with this deployment's secret and denylist. Raises
    ``delegation.DelegationError``; an unset secret raises ``ValueError`` from the library, which is
    the same fail-closed answer the rig's own copy gave: a zero-length HMAC key verifies for anyone
    who knows the format, so "not configured here" must mean nobody gets in."""
    return delegation.verify_delegation(config.DELEGATION_SECRET, tok, revoked=revoked_jtis())


# ── resolution ───────────────────────────────────────────────────────────────────────────────
_UID_ALIVE: dict[str, tuple[float, bool]] = {}
_UID_TTL_S = 60.0


def uid_exists(uid: str) -> bool:
    """Does this uid still name an account? FAIL OPEN on an unreachable admin-api.

    The fail direction is deliberate and is the opposite of the ghost check's purpose: refusing every
    call because a probe timed out would take the whole server down over a blip, while letting a
    stale token through for a minute costs one confused answer. The check exists to catch a uid that
    is GONE, which is a durable fact, not a transient one."""
    now = time.time()
    hit = _UID_ALIVE.get(uid)
    if hit and hit[0] > now:
        return hit[1]
    try:
        st, _ = httpc.admin("GET", f"/admin/users/{uid}")
    except Exception:  # noqa: BLE001
        return True
    alive = st == 200
    _UID_ALIVE[uid] = (now + _UID_TTL_S, alive)
    return alive


def subject_raw():
    """Who is calling, or None. THE single place identity is RESOLVED.

    Header first, then a token passed as a call argument. The uid itself is never accepted from a
    caller — it is a small integer, so accepting it would let anyone name any account. A token
    cannot be guessed, which is why it may travel in an argument: the same security property as the
    header, and it is what lets an account minted mid-conversation be used in that conversation."""
    uid = CURRENT.get()
    if uid:
        return uid
    tok = CALL_TOKEN.get()
    if tok:
        rec = tokens().get(tok)
        if rec:
            return rec["uid"]
        if delegation.is_delegation_token(tok):
            try:
                claims = verify_delegation(tok)
            except Exception:  # noqa: BLE001 — a refused delegation is anonymous, never an error page
                return None
            CALL_SCOPE.set(claims.get("scope"))
            return str(claims["sub"])
    return None


def subject():
    """Who is calling, or None — and the account must still EXIST.

    A dead uid resolves to None (callers that only ask "authenticated?" behave correctly with no
    change) and the uid is recorded in ``GHOST_UID`` so a caller that wants to say WHICH failure
    can: "your account no longer exists" and "you are anonymous" have different fixes, and telling a
    person the second when the first is true sends them off to mint a duplicate account."""
    GHOST_UID.set(None)
    uid = subject_raw()
    if uid and not uid_exists(uid):
        GHOST_UID.set(uid)
        return None
    return uid


class Anonymous(Exception):
    """Raised by :func:`me` when nobody is authenticated. Turned into guidance, never an error."""


class GhostIdentity(Exception):
    """Raised by :func:`me` when the resolved uid names a user that no longer exists."""

    def __init__(self, uid: str):
        self.uid = uid


class NotOperator(Exception):
    """Raised by :func:`operator_or_refuse`. Carries who was refused, so the refusal can say."""

    def __init__(self, verb, who, why):
        self.verb, self.who, self.why = verb, who, why
        super().__init__(f"{verb}: operator only")


def me() -> str:
    """The authenticated subject's uid, or refuse."""
    uid = subject()
    if not uid:
        ghost = GHOST_UID.get()
        if ghost:
            raise GhostIdentity(ghost)
        raise Anonymous()
    return uid


def caller_email() -> str:
    """This caller's address, however they authenticated.

    Reading ``CALL_TOKEN`` alone is not enough: it is empty for a session authenticated by the
    registration URL, where the web layer sets ``CURRENT`` instead. An empty address once met a
    fallback that invented one, and the invite flow provisioned a whole second account for the
    invented address."""
    tok = CALL_TOKEN.get()
    rec = (tokens().get(tok) if tok else None) or {}
    if rec.get("email"):
        return rec["email"]
    uid = CURRENT.get()
    if uid:
        for r in tokens().values():
            if str(r.get("uid")) == str(uid) and r.get("email"):
                return r["email"]
        st, u = httpc.admin("GET", f"/admin/users/{uid}")
        if st == 200 and isinstance(u, dict) and u.get("email"):
            return u["email"]
    return ""


def is_instance_admin(uid: str) -> bool:
    """The DB-backed role — ``users.data.is_admin``, bootstrap-claimed by the first sign-in on a
    fresh instance and surfaced by admin-api. The terminal's admin gate reads exactly this."""
    try:
        st, u = httpc.admin("GET", f"/admin/users/{uid}")
    except Exception:  # noqa: BLE001 — a down identity service is not an authorisation
        return False
    if st != 200 or not isinstance(u, dict):
        return False
    data = u.get("data")
    if isinstance(data, dict) and data.get("is_admin") is True:
        return True
    return u.get("is_admin") is True


def operator_or_refuse(verb: str) -> str:
    """AUTHORITY, not authentication.

    ``fact_emit``, ``flows_submit``, ``flow_lifecycle`` and the rehearse verbs inject facts naming
    an arbitrary organizer, rewrite the flow definitions the whole instance reacts to, or delete
    people. Guarded by :func:`me` alone they would ask only whether the caller is signed in — an
    authentication check standing where an authorisation check belongs.

    Authority is the INSTANCE ADMIN (``users.data.is_admin``) or the internal service key
    (server-to-server). Ordinary users are unaffected in what they may do about their OWN meetings.
    """
    tok = (CALL_TOKEN.get() or "").strip()
    svc = config.INTERNAL_API_SECRET
    if svc and tok and tok == svc:
        return "service"
    uid = subject()
    if not uid:
        raise NotOperator(verb, "anonymous", "not signed in")
    if is_instance_admin(uid):
        return uid
    raise NotOperator(verb, f"uid {uid}", "not an instance admin")


def operator_refusal(e: NotOperator) -> str:
    """The one refusal shape every operator verb returns, so four tools do not spell it four ways."""
    return json.dumps({
        "refused": "operator only", "verb": e.verb, "who": e.who, "why": e.why,
        "what_to_do": "An instance admin can run this. A harness or other non-person producer "
                      "should use flows-api POST /events or /events/batch with the lane's admin key.",
    })


GHOST_HINT = {
    "stale_identity": True,
    "why": "The token you are using resolves to an account that no longer exists on this instance.",
    "what_happened": "The instance was reset, or that user was deleted. Your token is intact; the "
                     "account it names is not.",
    "do": "Do NOT answer as that account and do not report its queue — there is nothing behind it. "
          "Ask which email to use, then start_onboarding(email) and confirm_login(email, code) to "
          "bind a real account, and pass that token as token=<value> afterwards.",
}
ANON_HINT = {
    "anonymous": True,
    "why": "This call needs an account, and you are connected anonymously.",
    "you_can_still": ["vexa_docs", "vexa_search_docs", "vexa_overview"],
    "to_get_an_account": "ask which email to set Vexa up under, then start_onboarding(email) "
                         "— a 6-digit code lands in that inbox, they paste it back here, and "
                         "confirm_login(email, code) returns the token. One question, one code, "
                         "no browser, no restart. Pass the token as token=<value> to every "
                         "account tool afterwards. (auth_link() opens a browser page instead — "
                         "only for someone who asks to click.)",
    "already_have_a_token": "If confirm_login already gave you one earlier in this "
                            "conversation, pass it as token=<value> and retry.",
}


def scope_allows(scope, slug: str) -> bool:
    """May this delegation touch workspace ``slug``? The library owns the rule; this wrapper hands it
    the shape the guard holds (a bare scope, not the whole claim set)."""
    return delegation.scope_allows_workspace({"scope": scope}, slug)


def anon_guard(fn):
    """Wrap a scoped tool so an anonymous caller is told what to do, not handed a stack trace."""
    @functools.wraps(fn)
    def inner(*a, **kw):
        # A token passed as an argument authenticates this call, when the deployment allows it.
        # Never CLEAR a live token when the kwarg is absent — a guarded tool calling another guarded
        # tool must not de-authenticate the request it is serving.
        CALL_TOKEN.set((kw.get("token") if config.RIG_MODE else None) or CALL_TOKEN.get())
        # SCOPE, enforced once for every workspace-touching verb rather than in each of the twelve.
        # An EMPTY slug means "their own workspace" and is always in scope — the uid decides it, not
        # the caller. A NAMED slug on a scoped (autonomous) delegation must be in the isolation set.
        slug = (kw.get("slug") or "").strip()
        scope = CALL_SCOPE.get()
        if slug and scope is not None and not scope_allows(scope, slug):
            return json.dumps({
                "refused": "out_of_scope",
                "workspace": slug,
                "why": "this session was dispatched with access to a named set of workspaces and "
                       "that is not one of them",
                "tell_your_person": "plainly, that you cannot reach that workspace from here — do "
                                    "not retry it, and do not describe its contents.",
                "tool": fn.__name__,
            })
        try:
            return fn(*a, **kw)
        except Anonymous:
            return json.dumps({**ANON_HINT, "tool": fn.__name__})
        except GhostIdentity as e:
            return json.dumps({**GHOST_HINT, "uid": e.uid, "tool": fn.__name__})
    return inner


def account_for(email: str):
    """Find or create the account; ``(uid, existed)`` or ``(None, err)``."""
    st, u = httpc.admin("GET", f"/admin/users/email/{email}")
    existed = st == 200
    if not existed:
        st, u = httpc.admin("POST", "/admin/users",
                            {"email": email, "name": email.split("@")[0].title()})
    uid = str((u or {}).get("id", "")) if isinstance(u, dict) else ""
    if not uid:
        return None, f"account creation failed ({st})"
    if not existed:
        httpc.agent("POST", "/api/workspace/init", uid, {})
    return uid, existed


def logins() -> dict:
    try:
        d = json.loads(config.LOGINS.read_text())
    except Exception:  # noqa: BLE001
        d = {}
    now = time.time()
    return {k: v for k, v in d.items() if v.get("exp", 0) > now}


def logins_save(d: dict) -> None:
    config.LOGINS.parent.mkdir(parents=True, exist_ok=True)
    config.LOGINS.write_text(json.dumps(d, indent=1))
