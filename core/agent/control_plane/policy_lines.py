"""policy_lines.py — THE POLICY-LINE REGISTRY (PRD 0.13.4 D38, SF-5), S1 floor.

Every policy line is typed: ``grant`` (fails closed even with a default), ``tuning`` (a value with a
default the workspace may change) or ``floor`` (a minimum the platform keeps: no workspace or user
line may lower it). The floors are enforced in the one evaluator (``policy_decide.GLOBAL_RULES``
carries a DENY for each floor's action, and a platform deny beats any workspace allow), and a
workspace rule set or line map that would lower one is refused before it is written
(``lowered_floors``; ``policy_proposals.normalize``).

Seeded floors (D38):
  * ``guest_access`` = ``refused`` — a guest is refused until audience-limited mounts exist (FD7);
  * ``nudge_body`` = ``template_only`` — a nudge email carries template fields only (D15);
  * ``destructive_first_tap`` = ``confirm`` — no destructive decision on the first tap, no swipe
    decisions (R11).
"""
from __future__ import annotations

GRANT, TUNING, FLOOR = "grant", "tuning", "floor"

#: line -> {type, value (the floor or the default), action (what the floor denies)}
REGISTRY: dict = {
    "guest_access": {"type": FLOOR, "value": "refused", "action": "guest.*"},
    "nudge_body": {"type": FLOOR, "value": "template_only", "action": "nudge.free_text"},
    "destructive_first_tap": {"type": FLOOR, "value": "confirm", "action": "card.destructive_first_tap"},
    # S5 (D11, D14): the card lifecycle templates and card tuning, seeded with their defaults. crm-api
    # reads them from `_global/POLICIES.md` (`card_templates.<kind>`, `card_check_hours`,
    # `card_seen_visible_to_sender`); a template line renames stages only — who may pass a gate is
    # the platform's (crm `card_templates.py`), never a line.
    "card_templates.commitment": {"type": TUNING,
                                  "value": "proposed > accepted > delivered > confirmed > closed | declined | expired"},
    "card_templates.approval": {"type": TUNING, "value": "opened > decided > executed > closed | declined | expired"},
    "card_templates.question": {"type": TUNING, "value": "asked > answered > used > closed | declined | expired"},
    "card_check_hours": {"type": TUNING, "value": "24"},
    "card_seen_visible_to_sender": {"type": TUNING, "value": "false"},
}


def floor_rules() -> tuple:
    """The evaluator's platform DENY for each floor (``policy_decide.GLOBAL_RULES``)."""
    return tuple({"id": f"floor.{name}", "effect": "deny", "action": spec["action"]}
                 for name, spec in REGISTRY.items() if spec["type"] == FLOOR)


def lowered_floors(*, rules=None, lines=None) -> list[str]:
    """The floors a workspace rule list or line map would lower (empty = none). A rule that ALLOWS a
    floor's action, or a line that names a floor with any other value, lowers it."""
    import fnmatch  # noqa: PLC0415
    out: list[str] = []
    for name, spec in REGISTRY.items():
        if spec["type"] != FLOOR:
            continue
        for r in rules or []:
            if (isinstance(r, dict) and r.get("effect") == "allow"
                    and (fnmatch.fnmatchcase(spec["action"], str(r.get("action") or ""))
                         or fnmatch.fnmatchcase(str(r.get("action") or ""), spec["action"]))):
                out.append(name)
                break
        if isinstance(lines, dict) and name in lines and str(lines[name]) != spec["value"]:
            if name not in out:
                out.append(name)
    return out
