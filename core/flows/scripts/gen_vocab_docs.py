#!/usr/bin/env python3
"""Generate the step-vocabulary docs page FROM the registry docstrings — the contract lives in
the code, the page is derived; run after step changes (make vocab-docs).

The page is MDX, and docstrings are prose written for Python readers: `refs.{a,b}`, `Result: {x}`
and `meet-<id>` are plain text there, but MDX reads `{…}` as a JavaScript expression and `<word>`
as a JSX tag. One unparseable docstring broke the whole docs build (2026-10-10), and a parseable
one is worse — `{uid}` silently renders as nothing. So every brace and every `<` that opens a tag
is escaped outside inline code spans, which MDX already shows verbatim."""
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[3] / "docs" / "docs" / "flows" / "vocabulary.mdx"

_CODE_SPAN = re.compile(r"(`[^`]*`)")
_BRACE = re.compile(r"(?<!\\)([{}])")
_TAG_OPEN = re.compile(r"(?<!\\)<(?=[a-z])")


def escape_mdx(text: str) -> str:
    """Escape `{`, `}` and a tag-opening `<` everywhere except inside `inline code`."""
    out = []
    for part in _CODE_SPAN.split(text):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(part)
        else:
            out.append(_TAG_OPEN.sub(r"\\<", _BRACE.sub(r"\\\1", part)))
    return "".join(out)


def render(steps: dict) -> str:
    """The page for a ``{name: step_fn}`` mapping — pure, so a test can check it without writing."""
    lines = ['---', 'title: "Step vocabulary"',
             'description: "The deployed capabilities flows compose — generated from the step '
             'docstrings in the image; every name here is submittable, nothing here is folklore."',
             '---', '',
             'A flow is a list of these names. This page is **generated from the registry** '
             '(`make vocab-docs`) — the docstring in the code is the contract, and `GET /flows` '
             'serves the same text at runtime.', '']
    for name in sorted(steps):
        doc = " ".join((steps[name].__doc__ or "⚠ undocumented — fix the docstring").split())
        lines.append(f"### `{name}`\n\n{escape_mdx(doc)}\n")
    return "\n".join(lines)


def registry_steps() -> dict:
    """The FULL product's steps — every domain present, no operator pack — whatever the caller's
    shell holds. Which steps register depends on configuration (`domain_present` reads the door
    attributes; `VEXA_FLOWS_DEFS_EXTRA` adds packs), so without pinning it a regen from a bare
    shell silently dropped the agent half from the page, and one from a stack's env added a pack."""
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from flows import Registry
    from flows_defs import production
    from flows_steps import common
    from sqlite_double import SqliteDB

    saved = (common.AGENT_API, common.MEETINGS_API, os.environ.pop(production.DEFS_EXTRA_ENV, None))
    common.AGENT_API, common.MEETINGS_API = "http://agent.invalid", "http://meetings.invalid"
    try:
        reg = Registry()
        production.build(reg, SqliteDB())
        return dict(reg.steps)
    finally:
        common.AGENT_API, common.MEETINGS_API = saved[0], saved[1]
        if saved[2] is not None:
            os.environ[production.DEFS_EXTRA_ENV] = saved[2]


if __name__ == "__main__":
    steps = registry_steps()
    OUT.write_text(render(steps))
    undocumented = [n for n in steps if not steps[n].__doc__]
    print(f"wrote {OUT.name} · {len(steps)} steps · undocumented: {undocumented or 'none'}")
