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
ALLOWLIST: dict[str, str] = {
    # ZAKI Minutes audio-TTL erasure — a DELIBERATE, reviewed exception, not an oversight. The S3
    # delete_prefix/count_prefix run INSIDE the retention DB transaction so the object deletion and the
    # `expired_scopes` mark commit-or-fail together (atomic erasure: a residue count > 0, or any failure,
    # rolls back BEFORE the scope is marked, so the next sweep re-runs it — no half-erased state is ever
    # observable). `test_ttl_store_expires_only_owned_audio_under_the_meeting_write_barrier` enforces this
    # single-lock atomic shape; a naive two-phase (delete-then-mark) split trades the atomicity away and is
    # unsafe. The correct evolution is the issue-#26 erasing-state pattern (mark-erasing → drain → delete →
    # finalize), a dedicated WP. Batch is bounded (≤500) and Minutes is dark, so the held-lock cost is fine.
    "src/meeting_api/retention/ttl_adapters.py:209": "audio-TTL: S3 delete under the retention tx — atomic mark-expired+delete (barrier test enforces it; #26 erasing-state is the proper evolution)",
    "src/meeting_api/retention/ttl_adapters.py:212": "audio-TTL: S3 residue-count under the retention tx — atomic verification before the expired mark",
}

# Param is a live DB session if named one of these OR annotated with a name ending in "Session".
_SESSION_PARAM_NAMES = {"db", "session", "conn", "connection"}


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


_GATHER_FUNCS = {"gather", "wait", "as_completed"}


def _passes_alias(await_node: ast.Await, alias: str) -> bool:
    """True if the awaited call is a genuine DELEGATION of the live session to DB-only work — not a
    concurrent non-DB coroutine that merely mentions the session.

    * ``asyncio.gather``/``wait``/``as_completed``(...): safe ONLY if EVERY coroutine argument is
      itself rooted at the session alias (all DB work). If any arg's root is not the alias — e.g.
      ``gather(self._redis.hgetall(k), db.execute(q))`` — a non-session coroutine runs WHILE the
      transaction is held, which is exactly FM03; not delegation → the caller flags it. (A ``*tasks``
      splat is unprovable → not delegation.)
    * any other call: delegation iff a TOP-LEVEL argument is exactly ``Name(alias)`` — ``helper(db)``
      or ``helper(session=db)``, where the callee receives the session and is gated by Rule 2. A
      merely-nested mention (``foo(bar(db))``) is NOT delegation (the outer call is not DB work)."""
    val = await_node.value
    if not isinstance(val, ast.Call):
        return False
    f = val.func
    fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    if fname in _GATHER_FUNCS:
        coro_args = [a for a in val.args if not isinstance(a, ast.Starred)]
        if len(coro_args) != len(val.args):  # a *splat is present — cannot prove all are DB work
            return False
        return bool(coro_args) and all(_root_name(a) == alias for a in coro_args)
    for arg in list(val.args) + [kw.value for kw in val.keywords]:
        if isinstance(arg, ast.Name) and arg.id == alias:
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
        # session_factory()/sessionmaker() AND engine.begin()/session.begin() — the ways a session or
        # transaction context is opened in this package (begin() covers the engine.begin() shape).
        if name.endswith("session_factory") or name in ("sessionmaker", "begin"):
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


def _first_await(src: str) -> ast.Await:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Await))


def test_gather_mixing_redis_and_db_is_not_delegation():
    """Closes the gate's asyncio.gather false negative: a gather that runs a NON-session coroutine
    concurrently with the held transaction is NOT delegation → R1 flags it (root != alias and not a
    pass-through). This is the FM03 defect hidden inside a gather."""
    aw = _first_await("import asyncio\nasync def f(self, db):\n await asyncio.gather(self._redis.hgetall('k'), db.execute('q'))\n")
    assert _root_name(aw) != "db"           # rooted at asyncio, so R1/R2 examine it
    assert _passes_alias(aw, "db") is False  # NOT treated as delegation → flagged


def test_gather_of_only_db_calls_is_allowed():
    """Control for the above: a gather whose every coroutine is session-rooted IS delegation (safe
    concurrent DB work) and must NOT be flagged — otherwise the rule would false-positive."""
    aw = _first_await("import asyncio\nasync def f(self, db):\n await asyncio.gather(db.execute('a'), db.execute('b'))\n")
    assert _passes_alias(aw, "db") is True


def test_conn_named_session_helper_is_scanned_by_r2():
    """Closes the 'helper receives the session under a non-standard name' false negative: a helper
    taking `conn` (not `db`/`session`) that awaits redis is now caught by R2."""
    src = ("class S:\n"
           "    async def helper(self, conn, meeting):\n"
           "        await conn.execute('q')\n"
           "        await self._redis.hgetall('k')\n")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef))
    assert "conn" in _session_params(fn)
    bad = [aw for aw in _awaits_in(fn) if _root_name(aw) not in {"conn"} and not _passes_alias(aw, "conn")]
    assert len(bad) == 1 and bad[0].lineno == 4  # the redis await, not conn.execute
