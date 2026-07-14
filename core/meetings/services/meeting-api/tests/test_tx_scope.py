"""#508 · C2 — the tx-scope gate (FM03: a DB transaction held open across an awaited non-DB call).

A stdlib-``ast`` gate over ``src/meeting_api/**/*.py``. It fails on either shape of the defect:

  Rule 1 (lexical) — an ``await`` INSIDE an ``async with … session_factory() as <db> …`` block whose
      awaited expression is neither rooted at ``<db>`` NOR passes ``<db>`` as an argument. The second
      carve lets a block delegate to a DB-only helper (``await self._x(db, …)``) without a false
      positive — that helper is itself checked by Rule 2.

  Rule 2 (semantic) — an ``async def`` that RECEIVES a live session (a param named ``db``/``session``
      or annotated ``*Session``) and awaits anything not rooted at that param (and not passing it
      along). This is the rule that catches the helper indirection a purely lexical scan misses:
      the pre-fix ``_transcript_doc(self, db, meeting)`` awaited ``self._redis.hgetall`` while holding
      the caller's session — the exact 2026-07-09 lock-convoy source.

No new dependencies — stdlib ``ast`` only, so the gate runs in meeting-api's SQLAlchemy-free test
venv. It is static and cannot see dynamic dispatch; the live ``pg_stat_activity`` probe (issue A2)
corroborates it. A legitimate future case is added to ALLOWLIST as a reviewed decision — never a
silent hole.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Package source root (…/meeting-api/src/meeting_api).
SRC = Path(__file__).resolve().parent.parent / "src" / "meeting_api"

# Reviewed exceptions: "<relpath>:<lineno>" → one-line justification. Empty by design at ship.
ALLOWLIST: dict[str, str] = {}

# Param is a live DB session if named one of these OR annotated with a name ending in "Session".
_SESSION_PARAM_NAMES = {"db", "session"}


def _root_name(node: ast.AST) -> str | None:
    """The base ``Name`` of a call/attribute/subscript chain (unwrapping an ``Await`` first).
    ``await db.execute(...)`` → ``db``; ``await self._redis.hgetall(...)`` → ``self``."""
    n = node
    while True:
        if isinstance(n, ast.Await):
            n = n.value
        elif isinstance(n, ast.Call):
            n = n.func
        elif isinstance(n, ast.Attribute):
            n = n.value
        elif isinstance(n, ast.Subscript):
            n = n.value
        else:
            break
    return n.id if isinstance(n, ast.Name) else None


def _passes_alias(await_node: ast.Await, alias: str) -> bool:
    """True if the awaited call passes ``alias`` anywhere in its arguments — i.e. delegates the live
    session to a helper (that helper is separately gated by Rule 2). Also covers ``asyncio.gather``
    /comprehension shapes where the db work is nested inside the call args."""
    val = await_node.value
    if not isinstance(val, ast.Call):
        return False
    for arg in list(val.args) + [kw.value for kw in val.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Name) and sub.id == alias:
                return True
    return False


def _session_params(fn: ast.AST) -> set[str]:
    """Names of parameters that carry a live session (by name or ``*Session`` annotation)."""
    names: set[str] = set()
    args = fn.args
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        if a.arg in _SESSION_PARAM_NAMES:
            names.add(a.arg)
        ann = a.annotation
        # unwrap Optional[AsyncSession] etc.
        for sub in ast.walk(ann) if ann is not None else []:
            if isinstance(sub, ast.Name) and sub.id.endswith("Session"):
                names.add(a.arg)
            if isinstance(sub, ast.Attribute) and sub.attr.endswith("Session"):
                names.add(a.arg)
    return names


def _awaits_in(node: ast.AST):
    """Yield every ``Await`` under ``node`` WITHOUT descending into nested function bodies (those
    have their own parameter scope and are visited on their own)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Await):
            yield child
        yield from _awaits_in(child)


def _is_session_ctx(item: ast.withitem) -> str | None:
    """If a ``with`` item opens a ``*session_factory()`` context bound to a name, return that name."""
    ctx = item.context_expr
    if isinstance(ctx, ast.Call):
        f = ctx.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name.endswith("session_factory") or name == "sessionmaker":
            if isinstance(item.optional_vars, ast.Name):
                return item.optional_vars.id
    return None


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    rel = str(path.relative_to(SRC.parent.parent))
    tree = ast.parse(path.read_text(), filename=str(path))
    findings: list[tuple[int, str, str]] = []

    def _record(lineno: int, rule: str, detail: str):
        if f"{rel}:{lineno}" in ALLOWLIST:
            return
        findings.append((lineno, rule, detail))

    # Rule 1 — awaits lexically inside a session_factory() block.
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            aliases = {a for a in (_is_session_ctx(it) for it in node.items) if a}
            if not aliases:
                continue
            for aw in _awaits_in(node):
                root = _root_name(aw)
                if root in aliases:
                    continue
                if any(_passes_alias(aw, a) for a in aliases):
                    continue
                _record(aw.lineno, "R1",
                         f"await inside session block ({'/'.join(sorted(aliases))}) not rooted at "
                         f"the session (root={root!r})")

    # Rule 2 — async defs that receive a live session and await non-session I/O.
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        params = _session_params(node)
        if not params:
            continue
        for aw in _awaits_in(node):
            root = _root_name(aw)
            if root in params:
                continue
            if any(_passes_alias(aw, p) for p in params):
                continue
            _record(aw.lineno, "R2",
                    f"{node.name}() holds a live session ({'/'.join(sorted(params))}) and awaits "
                    f"non-session I/O (root={root!r})")

    return findings


def scan_package() -> list[str]:
    """All findings across the package as sorted 'relpath:line [rule] detail' strings."""
    out: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC.parent.parent))
        for lineno, rule, detail in _scan_file(path):
            out.append(f"{rel}:{lineno} [{rule}] {detail}")
    return sorted(out)


def test_no_session_held_across_non_db_await():
    """The FM03 pattern must not exist anywhere in meeting_api. Red at base (one finding:
    `_transcript_doc` awaiting `self._redis.hgetall` with a live session), green at head."""
    findings = scan_package()
    assert findings == [], "tx-scope violations (session held across non-DB await):\n" + "\n".join(findings)


def test_gate_detects_a_planted_violation(tmp_path):
    """Negative control: the gate actually fires on the defect shape (so a green run means clean,
    not broken). Plants a helper that takes `db` and awaits redis, and asserts R2 catches it."""
    src = (
        "import ast\n"
        "class S:\n"
        "    async def bad(self, db):\n"
        "        await db.execute('x')\n"
        "        await self._redis.hgetall('k')\n"  # the violation
    )
    # Use the ast helpers directly (not _scan_file, whose relpath is computed against the package root).
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    assert _session_params(fn) == {"db"}
    bad_awaits = [aw for aw in _awaits_in(fn)
                  if _root_name(aw) not in {"db"} and not _passes_alias(aw, "db")]
    assert len(bad_awaits) == 1 and bad_awaits[0].lineno == 5
