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
import hashlib
import os
import re
import sys
from typing import Dict, List, Optional, Set

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


# ───────────────────────── Checkmark preservation ─────────────────────────

ITEM_HASH_RE = re.compile(r"^- \[([ x])\] (.+?)(?:\s*<!--\s*h:([0-9a-f]{8})\s*-->)?\s*$",
                          re.MULTILINE)


def _item_hash(text: str) -> str:
    """Stable 8-char hash of an item's visible text, ignoring the hash marker
    itself. Substituted {vm_ip} etc. are part of the hash — if the VM changes,
    checkmarks reset (correct: the target changed)."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(normalized.encode()).hexdigest()[:8]


def _load_prior_ticks(path: str) -> Dict[str, bool]:
    """Read an existing checklist and return {hash: checked_bool} for every
    tagged item. Untagged items are ignored (fresh generation)."""
    if not os.path.isfile(path):
        return {}
    ticks: Dict[str, bool] = {}
    with open(path) as f:
        content = f.read()
    for m in ITEM_HASH_RE.finditer(content):
        checked = m.group(1) == "x"
        h = m.group(3)
        if h:
            ticks[h] = checked
    return ticks


def _tag_items(rendered: str, prior: Dict[str, bool]) -> str:
    """After `generate` produces the markdown, walk each `- [ ] …` line,
    append ` <!-- h:XXXXXXXX -->`, and if the hash exists in `prior` as
    checked, flip `[ ]` → `[x]`."""
    out_lines: List[str] = []
    preserved = 0
    for line in rendered.splitlines():
        m = re.match(r"^- \[ \] (.+)$", line)
        if not m:
            out_lines.append(line)
            continue
        body = m.group(1).strip()
        # Strip any accidentally-present old marker so hash is stable
        body_bare = re.sub(r"\s*<!--\s*h:[0-9a-f]{8}\s*-->\s*$", "", body)
        h = _item_hash(body_bare)
        checked = prior.get(h, False)
        mark = "x" if checked else " "
        if checked:
            preserved += 1
        out_lines.append(f"- [{mark}] {body_bare} <!-- h:{h} -->")
    if preserved:
        print(f"  preserved {preserved} checkmark(s) from prior checklist", file=sys.stderr)
    return "\n".join(out_lines)


# ───────────────────────── Generate ─────────────────────────

def _mode_urls(mode: str, v: Dict[str, str]) -> List[str]:
    """Every URL a human needs for that mode — one bullet per URL, clickable."""
    out = []
    if mode == "lite" and "vm_ip" in v:
        ip = v["vm_ip"]
        out += [
            f"dashboard:   http://{ip}:3000",
            f"gateway:     http://{ip}:8056",
            f"admin:       http://{ip}:18056",
            f"ssh:         `ssh root@{ip}`",
        ]
    elif mode == "compose" and "vm_ip" in v:
        ip = v["vm_ip"]
        out += [
            f"dashboard:   http://{ip}:3001",
            f"/meetings:   http://{ip}:3001/meetings",
            f"/webhooks:   http://{ip}:3001/webhooks",
            f"gateway:     http://{ip}:8056",
            f"/docs:       http://{ip}:8056/docs",
            f"admin:       http://{ip}:18056",
            f"ssh:         `ssh root@{ip}`",
        ]
    elif mode == "helm":
        node = v.get("node_ip", "?")
        kc = v.get("lke_kubeconfig_path", "?")
        out += [
            f"dashboard:   http://{node}:30001",
            f"/meetings:   http://{node}:30001/meetings",
            f"gateway:     http://{node}:30056",
            f"kubectl:     `export KUBECONFIG={kc}`",
        ]
    return out


def generate(scope_path: str) -> str:
    with open(scope_path) as f:
        scope = yaml.safe_load(f)
    with open(ALWAYS_PATH) as f:
        always = yaml.safe_load(f)

    scope_modes: List[str] = list((scope.get("deployments") or {}).get("modes") or [])
    release_id = scope.get("release_id", "?")

    mode_vars = {m: load_mode_vars(m) for m in scope_modes}

    L: List[str] = []
    L.append(f"# {release_id} — human checklist\n")
    L.append(f"Tick boxes. `release-ship` blocks until all are `[x]`. "
             f"Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).\n")

    # URLs — one block per mode, every endpoint a human might click.
    L.append("## URLs")
    for m in scope_modes:
        L.append(f"\n**{m}**")
        for ln in _mode_urls(m, mode_vars[m]):
            L.append(f"- {ln}")
    L.append("")

    # ALWAYS — terse bullets grouped by mode. No preamble.
    L.append("## Always")
    for block in (always.get("always") or []):
        block_modes = set(block.get("modes") or [])
        applicable = [m for m in scope_modes if m in block_modes]
        if not applicable:
            continue
        L.append(f"\n**{block.get('section','')}**")
        for item in (block.get("items") or []):
            if len(applicable) == 1:
                L.append(f"- [ ] {fmt(item, mode_vars[applicable[0]])}")
            else:
                resolved = item
                for m in applicable:
                    resolved = fmt(resolved, mode_vars[m])
                    if "<unknown:" not in resolved:
                        break
                L.append(f"- [ ] {resolved}")
    L.append("")

    # THIS RELEASE — one line header per issue (id + required modes),
    # then terse `[mode] do → expect` items. No problem paragraphs.
    L.append("## This release")
    for issue in (scope.get("issues") or []):
        iid = issue.get("id", "?")
        required = ",".join(sorted(issue.get("required_modes") or [])) or "any"
        hv = issue.get("human_verify") or []
        if not hv:
            L.append(f"\n**{iid}** _({required})_ — no human-verify steps; trust the automated report.")
            continue
        L.append(f"\n**{iid}** _({required})_")
        for entry in hv:
            m = entry.get("mode", "")
            if m and m not in scope_modes:
                continue
            do = fmt(entry.get("do", ""), mode_vars.get(m, {}))
            expect = fmt(entry.get("expect", ""), mode_vars.get(m, {}))
            L.append(f"- [ ] [{m}] {do} → {expect}")
    L.append("")

    L.append("## Issues found")
    L.append("_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._")
    L.append("")

    return "\n".join(L)


# ───────────────────────── Gate ─────────────────────────

def gate(checklist_path: str) -> int:
    if not os.path.isfile(checklist_path):
        print(f"GATE FAILED: no checklist at {checklist_path}.", file=sys.stderr)
        print(f"  Run: make release-human-sheet SCOPE=<scope.yaml>", file=sys.stderr)
        return 1
    with open(checklist_path) as f:
        content = f.read()
    # Tolerate the optional trailing `<!-- h:XXXXXXXX -->` marker.
    unchecked = re.findall(r"^- \[ \] (.+?)(?:\s*<!--\s*h:[0-9a-f]{8}\s*-->)?\s*$",
                           content, flags=re.MULTILINE)
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
    gen.add_argument("--force", action="store_true",
                     help="Regenerate. Checkmarks on items whose text is unchanged are PRESERVED "
                          "(via per-item hash markers); only new/changed items reset to `- [ ]`. "
                          "Pass --wipe to discard all prior checkmarks instead.")
    gen.add_argument("--wipe", action="store_true",
                     help="Discard all prior checkmarks (implies --force).")

    g = sub.add_parser("gate", help="exit non-zero if any `- [ ]` remains")
    g.add_argument("--scope", required=True, help="Path to scope.yaml")
    g.add_argument("--checklist", help="Override checklist path (default: inferred from scope)")

    args = ap.parse_args()

    if args.cmd == "generate":
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.scope)), "human-checklist.md")
        exists = os.path.isfile(out)
        if exists and not (args.force or args.wipe):
            print(f"WARN: {out} already exists; not overwriting. "
                  f"Use --force to regenerate (preserves checkmarks for unchanged items).",
                  file=sys.stderr)
            return 0
        prior = {} if args.wipe else _load_prior_ticks(out)
        content = generate(args.scope)
        content = _tag_items(content, prior)
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
