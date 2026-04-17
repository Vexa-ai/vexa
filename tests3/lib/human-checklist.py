#!/usr/bin/env python3
"""
Generate (or gate) the human-validation checklist for a release.

Generates a markdown file at tests3/releases/<id>/human-checklist.md with
two parts:

  1. ALWAYS — static checks from tests3/human-always.yaml, same every release.
  2. THIS RELEASE — scope-specific checks from scope.issues[].human_verify[].

Variables like {vm_ip}, {node_ip}, {dashboard_url} are substituted from
each mode's tests3/.state-<mode>/ directory so the checklist has clickable
targets.

The checklist is the MERGE GATE. release-ship blocks until every `- [ ]`
becomes `- [x]`.

Commands:
  human-checklist.py generate --scope <path>      # write the checklist file
  human-checklist.py gate --scope <path>          # exit non-zero if any `- [ ]` remains
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: human-checklist.py requires PyYAML", file=sys.stderr)
    sys.exit(2)


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
T3 = os.path.join(ROOT, "tests3")
ALWAYS_PATH = os.path.join(T3, "human-always.yaml")


# ───────────────────────── Variable resolution ─────────────────────────

def load_mode_vars(mode: str) -> Dict[str, str]:
    """Read deployment-specific variables from tests3/.state-<mode>/ so we
    can substitute {vm_ip}, {node_ip}, {dashboard_url} into checklist items.
    """
    state_dir = os.path.join(T3, f".state-{mode}")
    v: Dict[str, str] = {"mode": mode}
    for name in ("vm_ip", "vm_id", "lke_node_ip", "lke_kubeconfig_path",
                 "gateway_url", "dashboard_url", "admin_url", "api_token",
                 "helm_release", "helm_namespace", "image_tag"):
        p = os.path.join(state_dir, name)
        if os.path.isfile(p):
            with open(p) as f:
                v[name] = f.read().strip()
    # Common aliases
    if "lke_node_ip" in v:
        v.setdefault("node_ip", v["lke_node_ip"])
    if "vm_ip" in v and "dashboard_url" not in v:
        port = 3000 if mode == "lite" else 3001
        v["dashboard_url"] = f"http://{v['vm_ip']}:{port}"
    return v


def fmt(template: str, vars: Dict[str, str]) -> str:
    """Substitute {name} placeholders; leave unknown ones visible as `<unknown:name>`."""
    def repl(m: re.Match) -> str:
        k = m.group(1)
        return vars.get(k, f"<unknown:{k}>")
    return re.sub(r"\{(\w+)\}", repl, template)


# ───────────────────────── Generate ─────────────────────────

def generate(scope_path: str) -> str:
    with open(scope_path) as f:
        scope = yaml.safe_load(f)
    with open(ALWAYS_PATH) as f:
        always = yaml.safe_load(f)

    scope_modes: List[str] = list((scope.get("deployments") or {}).get("modes") or [])
    release_id = scope.get("release_id", "?")
    summary = (scope.get("summary") or "").strip()

    # Resolve per-mode variables
    mode_vars = {m: load_mode_vars(m) for m in scope_modes}

    lines: List[str] = []
    lines.append(f"# Human validation — `{release_id}`\n")
    lines.append(f"> {summary}\n")
    lines.append("")
    lines.append("Check each box by editing `- [ ]` → `- [x]`. **`make release-ship` "
                 "refuses to run until every box is checked.** If something fails, "
                 "note it in the `## Issues found` section at the bottom and resolve "
                 "(either re-run the pipeline with a fix, or annotate the exception) "
                 "before merging.\n")

    # ── Deployment access table ──
    lines.append("## Access\n")
    lines.append("| Mode | URL | SSH / kubectl |")
    lines.append("|------|-----|---------------|")
    for m in scope_modes:
        v = mode_vars[m]
        if m == "lite" and "vm_ip" in v:
            lines.append(f"| lite | http://{v['vm_ip']}:3000 | `ssh root@{v['vm_ip']}` |")
        elif m == "compose" and "vm_ip" in v:
            lines.append(f"| compose | http://{v['vm_ip']}:3001 | `ssh root@{v['vm_ip']}` |")
        elif m == "helm":
            node = v.get("node_ip", "?")
            kubeconfig = v.get("lke_kubeconfig_path", "?")
            lines.append(f"| helm | http://{node}:30001 | `export KUBECONFIG={kubeconfig}` |")
    lines.append("")

    # ── Always checks ──
    lines.append("## ALWAYS — applies to every release\n")
    lines.append("_Source: `tests3/human-always.yaml`. These verify the product works regardless of what changed._\n")
    for block in (always.get("always") or []):
        block_modes = set(block.get("modes") or [])
        # Only emit if at least one of this block's modes is in scope
        applicable = [m for m in scope_modes if m in block_modes]
        if not applicable:
            continue
        section = block.get("section", "")
        lines.append(f"### {section}\n")
        # For cross-mode sections we substitute per applicable mode into each item;
        # for mode-specific blocks only the first applicable mode is used.
        for item in (block.get("items") or []):
            if len(applicable) == 1:
                lines.append(f"- [ ] {fmt(item, mode_vars[applicable[0]])}")
            else:
                # Cross-cutting: emit once, not per-mode, but resolve {vm_ip}
                # using the first mode that has it.
                resolved = item
                for m in applicable:
                    resolved = fmt(resolved, mode_vars[m])
                    if "<unknown:" not in resolved:
                        break
                lines.append(f"- [ ] {resolved}")
        lines.append("")

    # ── Scope-specific checks ──
    lines.append("## THIS RELEASE — scope-specific\n")
    lines.append(f"_Source: `{os.path.relpath(scope_path, ROOT)}` → `issues[].human_verify[]`._\n")
    for issue in (scope.get("issues") or []):
        iid = issue.get("id", "?")
        problem = (issue.get("problem") or "").strip().replace("\n", " ")
        required = ", ".join(sorted(issue.get("required_modes") or [])) or "(any)"
        lines.append(f"### `{iid}`  _(required modes: {required})_\n")
        lines.append(f"**Problem**: {problem}\n")
        hv = issue.get("human_verify") or []
        if not hv:
            lines.append("- [ ] _No explicit human-verify steps defined for this issue — confirm the automated report (§Scope status) reflects your observation on the deployed system._")
            lines.append("")
            continue
        for entry in hv:
            m = entry.get("mode", "")
            if m and m not in scope_modes:
                continue
            do = fmt(entry.get("do", ""), mode_vars.get(m, {}))
            expect = fmt(entry.get("expect", ""), mode_vars.get(m, {}))
            lines.append(f"- [ ] **[{m}]** Do: {do}  →  Expect: {expect}")
        lines.append("")

    # ── Tail sections ──
    lines.append("## Issues found\n")
    lines.append("_Leave empty if clean. Any bug surfaced here must be resolved "
                 "(fix + new pipeline run) before this checklist is signed off._\n")
    lines.append("")

    lines.append("## Sign-off\n")
    lines.append("- [ ] All ALWAYS items checked.")
    lines.append("- [ ] All THIS RELEASE items checked.")
    lines.append("- [ ] No unresolved entries in `Issues found`.")
    lines.append("")
    lines.append("Once all three boxes are checked AND `make release-full SCOPE=...` "
                 "succeeded, `make release-ship` is unblocked.\n")

    return "\n".join(lines)


# ───────────────────────── Gate ─────────────────────────

def gate(checklist_path: str) -> int:
    if not os.path.isfile(checklist_path):
        print(f"GATE FAILED: no checklist at {checklist_path}.", file=sys.stderr)
        print(f"  Run: make release-human-sheet SCOPE=<scope.yaml>", file=sys.stderr)
        return 1
    with open(checklist_path) as f:
        content = f.read()
    unchecked = re.findall(r"^- \[ \] (.+)$", content, flags=re.MULTILINE)
    if unchecked:
        print(f"GATE FAILED: {len(unchecked)} unchecked item(s) in {checklist_path}", file=sys.stderr)
        for item in unchecked[:10]:
            print(f"  - [ ] {item[:120]}", file=sys.stderr)
        if len(unchecked) > 10:
            print(f"  ... and {len(unchecked) - 10} more", file=sys.stderr)
        return 1
    # Also surface any "Issues found" body text as a warning (doesn't fail)
    m = re.search(r"^## Issues found\s*\n(.*?)(?=^## |\Z)", content, flags=re.MULTILINE | re.DOTALL)
    if m:
        body = m.group(1).strip()
        # strip the descriptive italic line
        body_lines = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("_")]
        if body_lines:
            print("NOTE: 'Issues found' section has content — verify each is resolved:", file=sys.stderr)
            for ln in body_lines[:5]:
                print(f"  {ln[:120]}", file=sys.stderr)
    print("GATE PASSED: human checklist signed off.", file=sys.stderr)
    return 0


# ───────────────────────── Entry ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Human-validation checklist generator + gate.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="write tests3/releases/<id>/human-checklist.md")
    gen.add_argument("--scope", required=True, help="Path to scope.yaml")
    gen.add_argument("--out", help="Override output path (default: same dir as scope, named human-checklist.md)")
    gen.add_argument("--force", action="store_true", help="Overwrite existing checklist (loses in-progress checkmarks)")

    g = sub.add_parser("gate", help="exit non-zero if any `- [ ]` remains")
    g.add_argument("--scope", required=True, help="Path to scope.yaml")
    g.add_argument("--checklist", help="Override checklist path (default: inferred from scope)")

    args = ap.parse_args()

    if args.cmd == "generate":
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.scope)), "human-checklist.md")
        if os.path.isfile(out) and not args.force:
            print(f"WARN: {out} already exists; not overwriting. Use --force to regenerate (resets checkmarks).", file=sys.stderr)
            return 0
        content = generate(args.scope)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            f.write(content)
        print(f"  wrote {out}", file=sys.stderr)
        print(f"  → fill in the checkboxes, then `make release-ship SCOPE={os.path.relpath(args.scope, ROOT)}`", file=sys.stderr)
        return 0

    if args.cmd == "gate":
        path = args.checklist or os.path.join(os.path.dirname(os.path.abspath(args.scope)), "human-checklist.md")
        return gate(path)

    return 2


if __name__ == "__main__":
    sys.exit(main())
