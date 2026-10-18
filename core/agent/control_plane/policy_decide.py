"""policy_decide.py — THE ONE POLICY EVALUATOR (PRD 0.13.4 D7), as an S1 stub.

One question, one answer: may ``principal`` do ``action`` in ``workspace``? → ``{decision, rule}``
where ``decision`` is ``allow`` · ``deny`` · ``refuse`` and ``rule`` is ``<rule id>@<sha>`` — the
rule that decided and the hash of the rule set it was read from, so a decision can be audited
against exactly what was in force. Served as ``POST /internal/policy/decide`` (internal tier) and
called in-process by agent-api's own routes; flows and crm gain the door in a later slice.

THE ORDER (D7): the platform's GLOBAL floor first, then the WORKSPACE's own rules (its rule file,
``policy/rules.json`` in git, platform-only: ``workspace_membership.read_rules``), then a card (S5 —
not here yet). A workspace rule can never widen the floor: a floor deny decides first.
**Deny beats allow** at every level and across levels: one matching deny decides, whatever allows.
No matching allow is a deny (``no_rule``).

FAIL CLOSED (founder FD3, 2026-10-17: "refuse"): a rule set that cannot be read answers
``refuse`` with reason ``policy_unreadable`` — never ``allow``, and never a silent ``deny`` that
looks like an ordinary no. The caller makes the fault visible.

⚠ INTERIM, AND SAID SO. D5's real gate on a policy change is a SIGNED POLICY CARD clicked with a
session credential. The CREDENTIAL CLASS now exists (D13): identity mints ``session`` only through
``POST /internal/sessions``, which only the terminal's sign-in can reach, and the gateway signs it
into ``x-user-credential-class``. So a policy change — and the approval of a policy proposal — now
needs the workspace's OWNER acting from a SESSION credential: no delegation token at all (a watched
agent is refused too, not only an unwatched one), and no API key, whatever its scopes (a self-serve
key with ``browser`` is still a key). The signed card (S5) comes later; when it lands, the
``policy.change`` allow below is replaced by "a signed card revision" and nothing else here changes
shape.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

ALLOW, DENY, REFUSE = "allow", "deny", "refuse"
POLICY_UNREADABLE = "policy_unreadable"

#: Credential classes the evaluator knows. ``session`` = a person at the terminal (identity's
#: ``credential_class=session``, D13); ``delegated`` = any worker, watched or not; ``key`` = every
#: other caller — any API key whatever its scopes, and a request whose class identity did not state;
#: ``internal`` = the internal tier.
SESSION, DELEGATED, KEY, INTERNAL = "session", "delegated", "key", "internal"

#: THE GLOBAL FLOOR, in code because it is the platform's own law (P20): what no workspace rule may
#: widen. Each rule: ``id``, ``effect``, ``action`` (glob), and optional ``credential`` (list),
#: ``role_min`` (owner > contributor > viewer), ``principal_kind`` (``person`` | ``ws``).
GLOBAL_RULES: tuple = (
    {"id": "floor.ws-principal-never-changes-policy", "effect": DENY, "action": "policy.*",
     "principal_kind": ["ws"]},
    {"id": "floor.policy-change-needs-a-session", "effect": DENY, "action": "policy.change",
     "credential": [DELEGATED, KEY]},
    # INTERIM (until S5's signed card): the owner, from a browser session.
    {"id": "interim.owner-session-changes-policy", "effect": ALLOW, "action": "policy.change",
     "credential": [SESSION], "role_min": "owner"},
    # ROOMS (0.13.4 S4). A guest seat (room-only, for a stranger) stays refused by the floor until
    # audience-limited mounts exist (FD7/D38): decision 9's seeded default is the workspace invite.
    {"id": "floor.no-guest-seats", "effect": DENY, "action": "room.guest_seat"},
    # "Always add when I mention" is a workspace line a person's click proposes (D21, I15); an agent's
    # mention never acts on it (deny test 29).
    {"id": "floor.agent-mentions-never-add", "effect": DENY, "action": "room.mention_adds_member",
     "credential": [DELEGATED, KEY]},
    # The room switches ("Vexa in this room", "Transcribe calls") are the room's to set by default; a
    # workspace DENY line on `room.switch.<name>.<on|off>` LOCKS one, and the lock names its rule.
    {"id": "default.room-switches", "effect": ALLOW, "action": "room.switch.*"},
    # "CALL <PERSON>" (call-person-1018): a person, or their agent while they are in the chat, starts a
    # Vexa call with someone they share a workspace with. A room's workspace principal never calls
    # anyone; a workspace DENY line on `call.person` stops calls started in its rooms.
    {"id": "floor.ws-principal-never-calls-a-person", "effect": DENY, "action": "call.person",
     "principal_kind": ["ws"]},
    {"id": "default.call-a-person", "effect": ALLOW, "action": "call.person"},
)
# THE FLOORS (D38, deny test 37): a platform deny per floor line, so no workspace allow lowers one.
from control_plane import policy_lines as _policy_lines  # noqa: E402
GLOBAL_RULES = GLOBAL_RULES + _policy_lines.floor_rules()

_RANK = {"viewer": 0, "reader": 0, "contributor": 1, "owner": 2}


class DecideBody(BaseModel):
    """The body of ``POST /internal/policy/decide``. An unstated credential is ``delegated``."""
    model_config = {"extra": "forbid"}
    principal: str = ""
    action: str
    workspace: str = ""
    role: Optional[str] = None
    credential: Optional[str] = None
    principal_kind: str = "person"


@dataclass(frozen=True)
class Decision:
    decision: str
    rule: str
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def as_dict(self) -> dict:
        return {"decision": self.decision, "rule": self.rule, "reason": self.reason}


@dataclass
class Ask:
    principal: str
    action: str
    workspace: str = ""
    role: Optional[str] = None
    credential: str = SESSION
    principal_kind: str = "person"
    extra: dict = field(default_factory=dict)


def rules_sha(rules) -> str:
    """The first 12 hex of the sha256 of the canonical rule set — the ``@sha`` in ``rule@sha``."""
    blob = json.dumps(list(rules), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def _matches(rule: dict, ask: Ask) -> bool:
    if not fnmatch.fnmatchcase(ask.action, str(rule.get("action") or "")):
        return False
    if rule.get("credential") and ask.credential not in rule["credential"]:
        return False
    if rule.get("principal_kind") and ask.principal_kind not in rule["principal_kind"]:
        return False
    if rule.get("principals") and str(ask.principal) not in [str(p) for p in rule["principals"]]:
        return False                                   # a person's own line (S4: "always" on mention)
    if rule.get("role_min"):
        if ask.role is None or _RANK.get(ask.role, -1) < _RANK.get(rule["role_min"], 99):
            return False
    return True


def _level(rules, ask: Ask) -> tuple[Optional[dict], Optional[dict]]:
    """``(first matching deny, first matching allow)`` within one level."""
    deny = next((r for r in rules if r.get("effect") == DENY and _matches(r, ask)), None)
    allow = next((r for r in rules if r.get("effect") == ALLOW and _matches(r, ask)), None)
    return deny, allow


def decide(ask: Ask, *, workspace_rules=None, root: Optional[Path] = None) -> Decision:
    """Evaluate ``ask``. ``workspace_rules`` is the workspace's rule list when the caller already has
    it; otherwise it is read from the policy store under ``root`` (an unreadable store → ``refuse``).
    Deny beats allow across both levels; nothing allowing is a deny."""
    g_sha = rules_sha(GLOBAL_RULES)
    if workspace_rules is None and root is not None and ask.workspace:
        from control_plane import workspace_membership as m  # noqa: PLC0415
        try:
            workspace_rules = m.read_rules(Path(root), ask.workspace)
        except m.MembershipError:
            return Decision(REFUSE, f"store@{g_sha}", POLICY_UNREADABLE)
        except Exception:  # noqa: BLE001 — anything unreadable refuses (FD3)
            return Decision(REFUSE, f"store@{g_sha}", POLICY_UNREADABLE)
    if workspace_rules is not None and not isinstance(workspace_rules, list):
        return Decision(REFUSE, f"store@{g_sha}", POLICY_UNREADABLE)
    ws_rules = [r for r in (workspace_rules or []) if isinstance(r, dict)]
    w_sha = rules_sha(ws_rules)
    g_deny, g_allow = _level(GLOBAL_RULES, ask)
    w_deny, w_allow = _level(ws_rules, ask)
    if g_deny:
        return Decision(DENY, f"{g_deny['id']}@{g_sha}", "denied by the platform floor")
    if w_deny:
        return Decision(DENY, f"{w_deny.get('id', 'workspace')}@{w_sha}", "denied by the workspace's rule")
    if g_allow:
        return Decision(ALLOW, f"{g_allow['id']}@{g_sha}")
    if w_allow:
        return Decision(ALLOW, f"{w_allow.get('id', 'workspace')}@{w_sha}")
    return Decision(DENY, f"no_rule@{g_sha}", "no rule allows this")


def credential_of(headers) -> str:
    """The credential class of a request (D13): delegated beats everything; a session only when
    identity said so (``x-user-credential-class: session``, rebuilt by the guard from the signed
    identity); anything else — any API key whatever its scopes, or no stated class — is a key."""
    from control_plane import identity_token  # noqa: PLC0415
    if identity_token.is_delegated(headers):
        return DELEGATED
    if identity_token.credential_class_of(headers) == identity_token.CREDENTIAL_SESSION:
        return SESSION
    return KEY
