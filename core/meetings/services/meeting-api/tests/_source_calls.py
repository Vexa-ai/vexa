"""Who calls what in meeting-api's own source, read with ``ast`` — the gates on the Meetings↔Flows
contract (ADR-0052, "The contract between Meetings and Flows") are written over it.

``sites(source, rel)`` lists every call in one module as a ``Site``: the dotted target it resolves
to through that module's imports (``meeting_api.events.publish_meeting_started``), the functions it
sits in, whether it is awaited, its keyword names, whether it passes ``**kwargs``, and its first
positional argument's target. ``refs(source, rel)`` lists every NAME the module uses — called,
passed, assigned or read — as a ``Ref`` with the same target and functions, so a function handed
on without being called is seen too. ``constants(source, rel)`` lists its string constants with
the functions they sit in. ``tree_sites()`` / ``tree_refs()`` do it for a whole tree, by default
every module under ``src/meeting_api`` (``modules()``); a gate passes its own map to test a plant.
A target the imports do not name resolves to ``<module>.<name>`` (a module-level function).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import meeting_api

SRC = Path(meeting_api.__file__).resolve().parent


@dataclass
class Site:
    rel: str
    target: str
    functions: tuple
    awaited: bool
    line: int
    keywords: frozenset = field(default_factory=frozenset)
    splat: bool = False
    first_arg: Optional[str] = None


def _module_of(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["meeting_api", *parts])


def _package_of(rel: str) -> list:
    mod = _module_of(rel).split(".")
    return mod if rel.endswith("__init__.py") else mod[:-1]


def _imports(tree: ast.AST, rel: str) -> dict:
    names: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = _package_of(rel)[: len(_package_of(rel)) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            for alias in node.names:
                names[alias.asname or alias.name] = f"{prefix}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0])
    return names


def _dotted(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _resolve(node: ast.AST, names: dict, module: str) -> Optional[str]:
    dotted = _dotted(node)
    if dotted is None:
        return None
    head, _, rest = dotted.partition(".")
    if head in names:
        return names[head] + (f".{rest}" if rest else "")
    return f"{module}.{dotted}"


def sites(source: str, rel: str) -> list:
    tree = ast.parse(source)
    names, module = _imports(tree, rel), _module_of(rel)
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve(node.func, names, module)
        if target is None:
            continue
        chain, up = [], parents.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(up.name)
            up = parents.get(up)
        first = _resolve(node.args[0], names, module) if node.args else None
        out.append(Site(rel=rel, target=target, functions=tuple(reversed(chain)),
                        awaited=isinstance(parents.get(node), ast.Await), line=node.lineno,
                        keywords=frozenset(k.arg for k in node.keywords if k.arg),
                        splat=any(k.arg is None for k in node.keywords), first_arg=first))
    return out


@dataclass
class Ref:
    rel: str
    target: str
    functions: tuple
    line: int


def _parents(tree: ast.AST) -> dict:
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _functions(node: ast.AST, parents: dict) -> tuple:
    chain, up = [], parents.get(node)
    while up is not None:
        if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(up.name)
        up = parents.get(up)
    return tuple(reversed(chain))


def refs(source: str, rel: str) -> list:
    """Every name used, outermost dotted form only (``events.publish`` once, not ``events`` too)."""
    tree = ast.parse(source)
    names, module, parents = _imports(tree, rel), _module_of(rel), _parents(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        up = parents.get(node)
        if isinstance(up, ast.Attribute) and up.value is node:
            continue
        target = _resolve(node, names, module)
        if target is not None:
            out.append(Ref(rel=rel, target=target, functions=_functions(node, parents),
                           line=node.lineno))
    return out


def constants(source: str, rel: str) -> list:
    """``(value, functions, line)`` for every string constant (docstrings included)."""
    tree = ast.parse(source)
    parents = _parents(tree)
    return [(n.value, _functions(n, parents), n.lineno) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def modules() -> dict:
    return {str(p.relative_to(SRC)): p.read_text() for p in sorted(SRC.rglob("*.py"))}


def tree_sites(mods: Optional[dict] = None) -> list:
    return [s for rel, src in (mods or modules()).items() for s in sites(src, rel)]


def tree_refs(mods: Optional[dict] = None) -> list:
    return [r for rel, src in (mods or modules()).items() for r in refs(src, rel)]


def planted(rel: str, code: str, mods: Optional[dict] = None) -> dict:
    """The tree with ``code`` appended to module ``rel`` — a gate's negative control."""
    out = dict(mods or modules())
    out[rel] = out[rel] + "\n\n" + code.strip() + "\n"
    return out
