"""policy_proposals.py — agents change policy WITH PERMISSION (PRD 0.13.4 D5; founder, 2026-10-17:
"agents CAN change policy, with permission").

A direct write to a workspace's policy stays refused (``policy/`` is reserved for every caller route,
the registry is written only by the platform). What an agent may do is PROPOSE: the exact change and
its reason are recorded as a ``policy`` proposal in the existing session proposal store
(``SessionStore.propose`` / ``decide`` — the store page proposals use), owned by the person entitled
to decide it, the workspace's owner. Nothing changes until that person APPROVES from a browser
session; the platform then APPLIES it through the one evaluator (``policy_decide``, D7) and the one
store (``workspace_membership``), and records who approved and on whose behalf it was proposed:
``Approved-by: <account> (proposal <id>)`` and ``On-behalf-of: <proposer>`` trailers on the policy
commit. A DECLINE changes nothing.

THE SEAM FOR S5. The signed policy card (D5, D13) wraps this same proposal: the card's revision
becomes the approval, and ``apply`` is unchanged. Only the approval check moves from "owner, browser
session" to "a signed card revision by the owner".

Changes an agent may propose:

  * ``visibility``     value ``open`` | ``closed``
  * ``connect``        value: the workspace to connect (open, same org — checked again on apply)
  * ``disconnect``     value: the workspace to stop reading
  * ``member_role``    value ``{"subject", "role"}`` — an existing member's role
  * ``member_remove``  value ``{"subject"}``
  * ``rules``          value: the workspace's whole rule list (``policy/rules.json``, git, platform-only)
"""
from __future__ import annotations

import json
from pathlib import Path
from control_plane import policy_decide as pd
from control_plane import workspace_membership as m
from workspaces.shared import workspace_paths as wpaths

CHANGES = ("visibility", "connect", "disconnect", "member_role", "member_remove", "rules")
REASON_MAX = 1000


def normalize(change: str, value):
    """The change in the form ``apply`` takes, or ``ValueError`` naming what is wrong."""
    if change not in CHANGES:
        raise ValueError(f"a policy change is one of {', '.join(CHANGES)}")
    if change == "visibility":
        if value not in ("open", "closed"):
            raise ValueError("visibility is open or closed")
        return value
    if change in ("connect", "disconnect"):
        if not wpaths.is_workspace_name(str(value or ""), tier=False):
            raise ValueError("name the workspace to connect or disconnect")
        return str(value)
    if change in ("member_role", "member_remove"):
        if not isinstance(value, dict) or not str(value.get("subject") or "").strip():
            raise ValueError("name the member (subject)")
        out = {"subject": str(value["subject"]).strip()}
        if change == "member_role":
            out["role"] = m.normalize_role(value.get("role"))
        return out
    if not isinstance(value, list) or not all(
            isinstance(r, dict) and r.get("id") and r.get("effect") in (pd.ALLOW, pd.DENY) and r.get("action")
            for r in value):
        raise ValueError("rules are a list of {id, effect: allow|deny, action}")
    from control_plane import policy_lines  # noqa: PLC0415
    lowered = policy_lines.lowered_floors(rules=value)
    if lowered:
        # D38: a floor is the platform's minimum; no workspace rule lowers it (deny test 37)
        raise ValueError(f"these are platform floors a workspace cannot lower: {', '.join(lowered)}")
    return value


def body_of(change: str, value, *, reason: str, on_behalf_of: str) -> str:
    return json.dumps({"change": change, "value": value, "reason": str(reason or "")[:REASON_MAX],
                       "on_behalf_of": on_behalf_of}, sort_keys=True)


def parse(body: str) -> dict:
    try:
        out = json.loads(body)
    except ValueError:
        raise ValueError("not a policy proposal") from None
    if not isinstance(out, dict) or out.get("change") not in CHANGES:
        raise ValueError("not a policy proposal")
    return out


def owner_of(root: Path, workspace: str) -> str:
    """Who decides a workspace's policy: its (first) owner."""
    return next((str(r.get("subject")) for r in m.read_members(root, workspace)
                 if r.get("role") == "owner" and r.get("subject")), "")


def _trailer_commit(approver: str, approver_email: str, proposal_id: str, on_behalf_of: str):
    base = m.policy_commit_as(approver, approver_email or f"{approver}@vexa.local")

    def _commit(ws: Path, message: str) -> None:
        base(ws, f"{message}\n\nApproved-by: {approver} (proposal {proposal_id})\n"
                 f"On-behalf-of: {on_behalf_of}")

    return _commit


def apply(root: Path, proposal: dict, *, approver: str, approver_email: str = "", credential: str,
          index: m.MembershipIndex) -> dict:
    """Apply an approved ``policy`` proposal. The approver must be the workspace's owner NOW and the
    evaluator must allow ``policy.change`` for them with ``credential``; otherwise nothing changes
    (``MembershipError`` 403, or 503 when the policy cannot be read)."""
    from control_plane import room_principal as rp  # noqa: PLC0415
    root = Path(root)
    ws = str(proposal.get("workspace") or "")
    spec = parse(proposal.get("body") or "")
    change, value = spec["change"], normalize(spec["change"], spec.get("value"))
    role = m.is_member(root, ws, approver)
    out = pd.decide(pd.Ask(principal=approver, action="policy.change", workspace=ws, role=role,
                           credential=credential), root=root)
    if out.decision == pd.REFUSE:
        raise m.MembershipError(f"{pd.POLICY_UNREADABLE}: {out.rule}", status=503)
    if not out.allowed:
        raise m.MembershipError(f"this change needs the workspace's owner, in the app ({out.rule})", status=403)
    commit_fn = _trailer_commit(approver, approver_email, str(proposal.get("id")),
                                str(spec.get("on_behalf_of") or ""))
    if change == "visibility":
        rp.set_visibility(root, ws, value, commit_fn=commit_fn)
    elif change == "connect":
        try:
            rp.connect(root, ws, value, actor=approver, commit_fn=commit_fn)
        except PermissionError as exc:
            raise m.MembershipError(str(exc), status=409) from None
    elif change == "disconnect":
        rp.disconnect(root, ws, value, commit_fn=commit_fn)
    elif change == "member_role":
        m.set_role(root, ws, value["subject"], value["role"], changed_by=approver, index=index,
                   commit_fn=commit_fn)
    elif change == "member_remove":
        m.remove_member(root, ws, value["subject"], index=index, commit_fn=commit_fn)
    else:
        target = m._ws_dir(root, ws)
        wpaths.write_text_inside(target, f"{m.POLICY_DIR}/rules.json",
                                 json.dumps(value, indent=2) + "\n", allow=(m.POLICY_DIR,))
        commit_fn(target, f"policy: rules for {ws}")
    return {"change": change, "value": value, "rule": out.rule}


def proposer_of(headers, subject: str) -> str:
    """Who proposed, for the ``On-behalf-of`` trailer: an agent acting for ``subject``, or ``subject``."""
    from control_plane import identity_token  # noqa: PLC0415
    if identity_token.is_delegated(headers):
        unit = identity_token.session_of(headers)
        return f"agent:{subject}" + (f" ({unit})" if unit else "")
    return str(subject)


__all__ = ["CHANGES", "apply", "body_of", "normalize", "owner_of", "parse", "proposer_of"]
