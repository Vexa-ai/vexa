"""GATE 14 — the standing check: NO TOOL IN THIS SURFACE WRITES A CREDENTIAL IN PLAINTEXT.

The three tests above prove today's stores are sealed. This one proves the NEXT one will be, which
is the part that actually failed: the encrypted store existed, was declared by decision 25, and
five separate writers in this file reached past it to `write_text(json.dumps(...))` at the default
umask because that was the shorter line. A rule that lives only in a review is a rule that holds
until the next hurry.

It reads the AST rather than the text, so a rename, a reflow or a comment quoting the old shape
does not move it, and it fails on the SHAPE — a credential-named target reaching a file write —
rather than on a list of known filenames.
"""
from __future__ import annotations

import ast
import pathlib
import re

RIG_DIR = pathlib.Path(__file__).resolve().parents[1]
GUARDED = ("vexa_control_mcp.py", "vexa_oauth.py")

#: Names that mean "this holds, or names, a credential". Deliberately broad — a false positive
#: costs one `rig_secrets` call, a false negative costs a credential.
CREDENTIALISH = re.compile(
    r"token|api_?key|_key\b|keys\b|secret|password|credential|passwd|"
    r"email_?codes?|sign_?in|login|oauth",
    re.I)

#: The store names each module declares. Each MUST be a plain string, never a path.
STORE_CONSTANTS = {
    "vexa_control_mcp.py": ["TOKENS_STORE", "EMAIL_CODES_STORE", "LOGINS_STORE",
                            "REGIMES_STORE", "USER_KEYS_STORE"],
    "vexa_oauth.py": ["CLIENTS", "CODES", "TOKENS"],
}

WRITE_METHODS = {"write_text", "write_bytes", "writelines"}


def _trees():
    for name in GUARDED:
        p = RIG_DIR / name
        yield name, p.read_text(), ast.parse(p.read_text())


def test_credential_stores_are_names_not_paths():
    """A store constant that is a `pathlib.Path` is a plaintext file waiting to be written to. Each
    of these used to be exactly that: `HOME / ".storm/mcp-tokens.json"` and friends."""
    bad = []
    for name, _src, tree in _trees():
        wanted = set(STORE_CONSTANTS[name])
        seen = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    seen.add(target.id)
                    if not (isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)):
                        bad.append(f"{name}:{node.lineno} {target.id} is not a store name")
        missing = wanted - seen
        assert not missing, f"{name}: store constants vanished: {sorted(missing)}"
    assert bad == [], bad


def _write_offences(name: str, src: str) -> list:
    """Every credential-named target reaching a file write in ``src``."""
    offences = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        target_src = ""
        if isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_METHODS:
            target_src = ast.get_source_segment(src, node.func.value) or ""
        elif isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = ""
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            if any(m in mode for m in ("w", "a", "x", "+")):
                target_src = ast.get_source_segment(src, node.args[0]) or ""
        if target_src and CREDENTIALISH.search(target_src):
            offences.append(f"{name}:{node.lineno} writes {target_src!r}")
    return offences


def test_the_gate_fails_on_the_shape_it_was_written_for():
    """A gate nobody has watched fail is a gate nobody knows the state of (this suite's R-C10
    lesson, applied to itself). These are the exact lines the review found, verbatim."""
    was = ('TOKENS_FILE = HOME / ".storm/mcp-tokens.json"\n'
           'def _mint(tok, uid):\n'
           '    d = {tok: uid}\n'
           '    TOKENS_FILE.write_text(json.dumps(d, indent=1))\n'
           '    open(EMAIL_CODES, "w").write("...")\n')
    assert len(_write_offences("was.py", was)) == 2, _write_offences("was.py", was)


def test_no_credential_named_target_is_written_to_a_file():
    """The shape itself: anything credential-named reaching `write_text`/`open(..., 'w')`.

    `rig_secrets` is the one module allowed to write a credential, and it seals before it does."""
    offences = []
    for name, src, _tree in _trees():
        offences += _write_offences(name, src)
    assert offences == [], (
        "a credential-named target is being written to a file directly; route it through "
        f"rig_secrets.write/update instead: {offences}")


def test_no_plaintext_credential_file_path_literals_remain():
    """The literal shape of the defect, so it cannot come back under a new name: a credential-named
    `.storm/*.json` string in the rig IS a plaintext store — that is the only thing that shape has
    ever been here (`mcp-tokens.json`, `user-api-keys.json`, `oauth/logins.json`,
    `oauth/email-codes.json`, `oauth/tokens.json`, `oauth/codes.json`)."""
    offences = []
    for name, _src, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # A BARE PATH literal, not a paragraph that happens to say "token" — the agent
                # instructions in this file are prose about `.mcp.json` and say so at length.
                if (re.fullmatch(r"[\w.~/-]+\.jsonl?", node.value)
                        and CREDENTIALISH.search(node.value)):
                    offences.append(f"{name}:{node.lineno} {node.value!r}")
    assert offences == [], offences


#: Thin wrappers that do nothing but forward to `rig_secrets` — proven to be exactly that below.
_WRAPPERS = {"vexa_oauth.py": {"_load", "_save"}, "vexa_control_mcp.py": set()}


def test_every_store_read_and_write_goes_through_rig_secrets():
    """The store names are arguments to `rig_secrets` (or to a wrapper that is nothing else), and
    to nothing more — so a future caller cannot reach one with `open()` and a name it built."""
    offences = []
    for name, src, tree in _trees():
        wanted = set(STORE_CONSTANTS[name])
        wrappers = _WRAPPERS[name]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and node.id in wanted
                    and isinstance(node.ctx, ast.Load)):
                continue
            parent = _parent_call(tree, node)
            f = parent.func if parent is not None else None
            ok = ((isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                   and f.value.id == "rig_secrets")
                  or (isinstance(f, ast.Name) and f.id in wrappers))
            if not ok:
                offences.append(f"{name}:{node.lineno} {node.id} used outside rig_secrets "
                                f"({ast.get_source_segment(src, parent) if parent else '?'})")
    assert offences == [], offences


def test_the_store_wrappers_forward_and_do_nothing_else():
    """The exemption above is only safe while it is true: each wrapper's whole body is one call
    into `rig_secrets`. A wrapper that grew a `write_text` would take the gate down with it."""
    for name, _src, tree in _trees():
        for fname in _WRAPPERS[name]:
            fn = next((n for n in ast.walk(tree)
                       if isinstance(n, ast.FunctionDef) and n.name == fname), None)
            assert fn is not None, f"{name}: wrapper {fname} is gone — drop it from _WRAPPERS"
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            assert calls, f"{name}:{fname} calls nothing"
            for c in calls:
                assert (isinstance(c.func, ast.Attribute)
                        and isinstance(c.func.value, ast.Name)
                        and c.func.value.id == "rig_secrets"), \
                    f"{name}:{fname} does more than forward to rig_secrets"


def _parent_call(tree, target):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(a is target for a in node.args):
            return node
    return None
