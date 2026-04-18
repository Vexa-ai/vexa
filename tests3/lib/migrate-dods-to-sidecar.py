#!/usr/bin/env python3
"""
Step 2 of release-system-review §4.3 — migrate DoDs from feature README
frontmatter into a per-feature `dods.yaml` sidecar.

For each `features/<name>/README.md`:
1. Read the YAML frontmatter between the first two `---` lines.
2. Extract `tests3.dods` and `tests3.gate` into `features/<name>/dods.yaml`.
3. Strip the `tests3:` block from frontmatter (keeping `services:` etc).
4. If frontmatter becomes empty, remove it entirely.
5. Insert a reference line near the top of the README body (if not present).

Idempotent: re-running after partial migration doesn't corrupt.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: needs PyYAML", file=sys.stderr)
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent.parent
FEATURES = ROOT / "features"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
REF_LINE_RE = re.compile(r"^\*\*DoDs:\*\* see \[`\./dods\.yaml`\]", re.MULTILINE)


def extract_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    rest = text[m.end():]
    return fm, rest


def write_frontmatter(fm: dict) -> str:
    if not fm:
        return ""
    body = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).rstrip() + "\n"
    return f"---\n{body}---\n"


def inject_ref_line(body: str, confidence_min: int) -> str:
    """Insert the DoD reference line after the first heading, if not already present."""
    if REF_LINE_RE.search(body):
        return body
    ref = f"\n**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence \u2265 {confidence_min}%**\n"
    # Insert after the first H1 (if any)
    lines = body.splitlines(keepends=True)
    out = []
    inserted = False
    for i, ln in enumerate(lines):
        out.append(ln)
        if not inserted and ln.startswith("# "):
            out.append(ref)
            inserted = True
    if not inserted:
        # No H1 found — prepend
        return ref + body
    return "".join(out)


def migrate(readme: Path) -> dict:
    feature = readme.parent.name
    text = readme.read_text()
    fm, body = extract_frontmatter(text)

    tests3 = fm.get("tests3") or {}
    dods = tests3.get("dods") or []
    gate = tests3.get("gate") or {"confidence_min": 90}

    sidecar = readme.parent / "dods.yaml"
    action = {"feature": feature, "dods_count": len(dods), "sidecar": str(sidecar.relative_to(ROOT))}

    if not sidecar.exists():
        if dods:
            content = {"gate": gate, "dods": dods}
            body = yaml.safe_dump(content, default_flow_style=False, sort_keys=False, width=120)
        else:
            # Per §3.2 / §3.5: a feature without dods.yaml is a hard fail;
            # the only opt-out is explicit `dods: []` with a reason. Emit that
            # opt-out stub so the feature passes the aggregator while leaving
            # a clear TODO to write real DoDs.
            body = (
                "# Intentionally un-gated: legacy feature carries no machine-readable\n"
                "# DoDs yet. Populate `dods:` before this feature's next release or\n"
                "# its expected behavior changes.\n"
                "gate:\n  confidence_min: 0    # not enforced until dods: is populated\n"
                "dods: []   # intentionally un-gated, reason: DoDs not yet authored\n"
            )
            action["opt_out_stub"] = True
        sidecar.write_text(body)
        action["wrote_sidecar"] = True
    else:
        action["wrote_sidecar"] = False
        action["sidecar_exists"] = True

    # Strip tests3 from frontmatter
    if "tests3" in fm:
        del fm["tests3"]
        action["stripped_tests3_frontmatter"] = True

    # Build new README content
    body = inject_ref_line(body, gate.get("confidence_min", 90))
    new_text = write_frontmatter(fm) + body
    if new_text != text:
        readme.write_text(new_text)
        action["readme_rewritten"] = True
    else:
        action["readme_rewritten"] = False

    action["status"] = "ok"
    return action


def main() -> int:
    # Top-level + nested (realtime-transcription/zoom, etc.)
    readmes = sorted(list(FEATURES.glob("*/README.md")) + list(FEATURES.glob("*/*/README.md")))
    actions = []
    for r in readmes:
        try:
            a = migrate(r)
        except Exception as e:
            a = {"feature": r.parent.name, "status": f"error: {e}"}
        actions.append(a)

    for a in actions:
        print(f"  {a['feature']:30} {a['status']}"
              + (f" — {a['dods_count']} dods" if 'dods_count' in a else ""))

    print()
    ok = sum(1 for a in actions if a.get("status") == "ok")
    skip = sum(1 for a in actions if a.get("status", "").startswith("skip"))
    err = sum(1 for a in actions if a.get("status", "").startswith("error"))
    print(f"  {ok} migrated, {skip} skipped, {err} errors")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
