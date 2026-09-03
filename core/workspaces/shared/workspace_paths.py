"""workspace_paths.py — ONE answer to "does this caller-supplied path stay inside this workspace".

Before this module the answer was spelled six times across five files and the spellings disagreed.
``link_resolver.escapes`` counted ``..`` segments and nothing else — and ``Path("/ws") / "/etc/passwd"``
is ``/etc/passwd``, because an ABSOLUTE path silently DISCARDS the root it was joined to. So
``[[ws:abcdefghij//etc/passwd]]`` walked past the guard, the resolver read the file and echoed
``path`` and ``url`` back to the client, which then handed both to the file endpoint (R-A06,
reachable on ``POST /api/links/resolve`` — the read path for any document a person opens).

The other five spellings each caught a different subset. That is the failure this module exists to
end: **a path from outside is refused for four reasons and every route gets all four.**

* **absolute** — the root is dropped, as above;
* **``..``** — the ordinary traversal, including one that only escapes after descending first;
* **a symlink out** — the string stays inside and the RESOLVED path does not, which is the shape no
  purely textual check can ever see;
* **a reserved directory** — ``.git`` (the repository's own store: history, and hooks that execute
  on the next commit) and ``.vexa`` (the workspace identity ``shared/workspace_id.py`` writes —
  overwrite it and the workspace mints a new id, so every ``[[ws:<id>/…]]`` link into it resolves
  ``gone``, PRD decision 26.1). A route that legitimately owns one names it in ``allow``.

Refusal is an EXCEPTION, never a ``None`` or a ``False``: of the six spellings this replaces, the
ones that returned a value each had at least one caller that did not look at it.
"""
from __future__ import annotations

from pathlib import Path

#: Directories no caller-supplied path may reach into. ``allow=(".git",)`` opens one for a route
#: that owns it — nothing does today; the parameter exists so a future one need not weaken the rule.
RESERVED_DIRS = (".git", ".vexa")


class PathRefused(ValueError):
    """A caller-supplied path that does not stay inside the workspace it names.

    ``kind`` says which of the four rules refused it — for a log line, never for the caller's
    response body, where one sentence for all four is the honest answer (a probe must not learn
    from the refusal WHY it was refused)."""

    def __init__(self, reason: str, *, kind: str) -> None:
        super().__init__(reason)
        self.kind = kind      # 'absolute' | 'traversal' | 'reserved' | 'symlink' | 'empty'


REFUSAL = "that path is not inside this workspace"


def relative_parts(path: str, *, allow=()) -> list[str]:
    """The path's segments, or ``PathRefused`` — the TEXTUAL half of the rule.

    Split out because two callers have no root to resolve against: ``link_resolver.escapes`` answers
    about a link before it knows which workspace it lands in, and ``desk_touch`` records a path it
    never opens. Both still get absolute · ``..`` · reserved."""
    rel = str(path or "").strip().replace("\\", "/")
    if not rel:
        raise PathRefused(REFUSAL, kind="empty")
    if rel.startswith("/") or Path(rel).is_absolute():
        raise PathRefused(REFUSAL, kind="absolute")
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise PathRefused(REFUSAL, kind="traversal")
    if not parts:
        raise PathRefused(REFUSAL, kind="empty")
    reserved = {d for d in RESERVED_DIRS if d not in set(allow or ())}
    if any(p in reserved for p in parts):
        raise PathRefused(REFUSAL, kind="reserved")
    return parts


def resolve_inside(root, path: str, *, allow=()) -> Path:
    """The absolute path ``path`` names INSIDE ``root``, or ``PathRefused``.

    ``root`` is resolved once and every comparison is made against the resolved value, so a store
    reached through a symlink is not itself an escape — only a link that leaves the workspace is."""
    parts = relative_parts(path, allow=allow)
    base = Path(root).resolve()
    target = (base / "/".join(parts)).resolve()
    if target != base and base not in target.parents:
        raise PathRefused(REFUSAL, kind="symlink")
    return target


def is_inside(root, path: str, *, allow=()) -> bool:
    """``resolve_inside`` as a predicate — for the read paths whose answer is "not found", not 400."""
    try:
        resolve_inside(root, path, allow=allow)
    except (PathRefused, OSError):
        return False
    return True
