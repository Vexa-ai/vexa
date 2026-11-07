"""kg_templates.py — THE COMPANY'S NOTE TEMPLATES, and the one rule that keeps them usable.

Founder ruling 2026-10-11: the deployment's admin account controls *"templates (so they can control
seed templates that agent creates new notes from)"*. An entity page — a person, a company, a
meeting, a project, a decision — is created as a card whose SHAPE is a template. The baked shapes
live in the image (`behavior/workspaces/default/kg/templates/`, seeded into every desk); the
company's live at `_global/kg/templates/<kind>.md`, seeded there by `global_seed` and written only by
the admin account. When the company has one for a kind, it is the shape: `entity_upsert` creates a
new card with its sections, and the worker is told to read it there first.

`kg/templates/` stays refused for every other writer and in every other workspace (a save from a
desk tab would rewrite one person's copy, which nothing reads as the company's shape).

VALIDATED ON WRITE, AND AGAIN ON READ. The write door refuses a template that would not work as one
— front matter that does not open the file, no `template: true`, a `type:` that is not the file's
kind, no `id:`/`title:` slot, or a section the field map files into removed. The reader
(`entities.template_sections`) applies the same test and falls back to the baked shape, because the
admin account's own agent can write `_global` through its mount without passing this door, and a
bad edit must never break note creation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from workspaces.shared import entities as entities_mod
from workspaces.shared import workspace_paths as wpaths

#: Where a template lives, relative to its workspace.
TEMPLATES_DIR = "kg/templates/"
#: The one file in the directory that is prose ABOUT the shapes rather than a shape.
README = "README.md"
#: How large a template may be. A shape is a page skeleton, not a document.
MAX_BYTES = 32 * 1024
#: The front-matter slots every kind template must keep: the writer fills them on every new card.
REQUIRED_SLOTS = ("type", "id", "title")


def kinds() -> tuple:
    """The kinds a template may shape — the ones `entity_upsert` creates cards for."""
    return tuple(entities_mod.CARD_SECTIONS)


def is_template_path(rel: str) -> bool:
    return str(rel or "").strip().lstrip("/").startswith(TEMPLATES_DIR)


def validate(rel: str, content: str) -> list[str]:
    """Every reason ``content`` cannot be the template at ``rel``; empty when it can.

    Each reason is a sentence the admin account's agent can act on, naming what to put back."""
    rel = str(rel or "").strip().lstrip("/")
    name = rel[len(TEMPLATES_DIR):] if rel.startswith(TEMPLATES_DIR) else rel
    if "/" in name or not name.endswith(".md"):
        return [f"a template is one file directly under {TEMPLATES_DIR}, named <kind>.md — "
                f"one of {', '.join(kinds())}"]
    text = content or ""
    problems: list[str] = []
    if len(text.encode("utf-8")) > MAX_BYTES:
        problems.append(f"a template is a page skeleton; keep it under {MAX_BYTES // 1024} KB")
    if not text.startswith("---\n"):
        return problems + ["the file must open with its front matter (`---` on the first line) — "
                           "if you read it through the workspace API, drop the template banner "
                           "that was prepended before writing it back"]
    fm, _body = entities_mod.split_frontmatter(text)
    if (entities_mod._fm_get(fm, "template") or "").strip().lower() != "true":
        problems.append("the front matter must keep `template: true` — it is what stops a shape "
                        "being read as a record")
    if name == README:
        return problems
    kind = name[:-3].lower()
    if kind not in kinds():
        return problems + [f"`{name}` names no kind a card is created for — the kinds are "
                           f"{', '.join(kinds())}"]
    for slot in REQUIRED_SLOTS:
        if entities_mod._fm_get(fm, slot) is None:
            problems.append(f"the front matter must keep a `{slot}:` line — every new {kind} card "
                            "fills it")
    if (entities_mod._fm_get(fm, "type") or "").strip().lower() != kind:
        problems.append(f"`type:` must be `{kind}`, the kind this file is named after")
    if not problems and entities_mod.template_sections(kind, text) is None:
        need = list(entities_mod.CARD_SECTIONS[kind]) + list(entities_mod.TAIL_SECTIONS)
        problems.append(f"a {kind} template must keep these sections as `## ` headings (you may add "
                        f"others and reorder them): {', '.join(need)}")
    return problems


def company_template(global_root: Optional[Path], kind: str) -> str:
    """The company's template for ``kind``, raw, or ``""`` — absent, unreadable, or no `_global`.

    Read nofollow: `_global` is written by an agent's turn, and a link planted at the file must not
    turn a template read into a read of something else."""
    kind = (kind or "").strip().lower()
    if global_root is None or kind not in kinds():
        return ""
    try:
        raw = wpaths.read_bytes_inside(Path(global_root), f"{TEMPLATES_DIR}{kind}.md")
    except (OSError, ValueError):   # ValueError: PathRefused
        return ""
    if raw is None or len(raw) > MAX_BYTES:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
