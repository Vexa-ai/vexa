#!/usr/bin/env python3
"""
tests3 report aggregator.

Reads:
  - tests3/test-registry.yaml — which tests exist, their tier, modes, features
  - tests3/.state/reports/<mode>/<test>.json — evidence artifacts from validate-<mode>
  - features/*/README.md frontmatter — DoD → evidence bindings + gate threshold

Outputs:
  - tests3/reports/release-<tag>.md — cross-deployment markdown report (committed)
  - Per-mode JSON summary at .state/reports/<mode>/summary.json
  - stdout: gate check verdict (with --gate-check)
  - In-place rewrite of feature README DoD tables (with --write-features)  [Phase D]

Exit codes:
  0 — report generated; gate (if requested) passes
  1 — one or more features fall below their gate.confidence_min or have a fail
  2 — usage / configuration error
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:
    print("ERROR: aggregate.py requires PyYAML (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
T3 = os.path.join(ROOT, "tests3")
STATE = os.path.join(T3, ".state")
REGISTRY_PATH = os.path.join(T3, "test-registry.yaml")
FEATURES_GLOB = os.path.join(ROOT, "features", "*", "README.md")
# Also pick up nested features like features/realtime-transcription/gmeet/README.md
NESTED_FEATURES_GLOB = os.path.join(ROOT, "features", "*", "*", "README.md")


# ─────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """One step/check result from a test report."""
    id: str
    status: str  # pass | fail | skip
    message: str = ""


@dataclass
class TestReport:
    """A single JSON artifact from one test run in one mode."""
    test: str
    mode: str
    status: str  # pass | fail
    image_tag: str = ""
    duration_ms: int = 0
    steps: Dict[str, StepResult] = field(default_factory=dict)  # step_id → StepResult

    @classmethod
    def load(cls, path: str) -> Optional["TestReport"]:
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            print(f"WARN: could not parse {path}: {e}", file=sys.stderr)
            return None
        steps = {}
        for s in data.get("steps", []):
            sid = s.get("id", "")
            if not sid:
                continue
            steps[sid] = StepResult(
                id=sid,
                status=s.get("status", "?"),
                message=s.get("message", ""),
            )
        return cls(
            test=data.get("test", os.path.basename(path).rsplit(".json", 1)[0]),
            mode=data.get("mode", ""),
            status=data.get("status", "?"),
            image_tag=data.get("image_tag", ""),
            duration_ms=int(data.get("duration_ms", 0)),
            steps=steps,
        )


@dataclass
class DoDEvidence:
    """A DoD's binding to a test step or check."""
    test: Optional[str] = None      # name of a test in test-registry.yaml
    step: Optional[str] = None      # step ID within that test
    check: Optional[str] = None     # alternative: check ID from checks/registry.json
    modes: List[str] = field(default_factory=list)  # [] = any mode that ran


@dataclass
class DoD:
    id: str
    label: str
    weight: int
    evidence: DoDEvidence
    # Computed after loading reports:
    status: str = "missing"   # pass | fail | skip | missing
    evidence_msgs: List[Tuple[str, str]] = field(default_factory=list)  # [(mode, message)]


@dataclass
class Feature:
    name: str
    path: str                          # README path
    gate_confidence_min: int = 0       # from frontmatter tests3.gate.confidence_min
    dods: List[DoD] = field(default_factory=list)
    # Computed:
    confidence: int = 0
    mode_status: Dict[str, str] = field(default_factory=dict)  # mode → pass/fail/partial


# ─────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────

def load_registry() -> Dict[str, Any]:
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f) or {}


def load_reports(modes: Optional[List[str]] = None) -> Dict[str, Dict[str, TestReport]]:
    """Return {mode: {test_name: TestReport}}."""
    if not os.path.isdir(os.path.join(STATE, "reports")):
        return {}
    out: Dict[str, Dict[str, TestReport]] = {}
    for mode_dir in sorted(os.listdir(os.path.join(STATE, "reports"))):
        if modes and mode_dir not in modes:
            continue
        mpath = os.path.join(STATE, "reports", mode_dir)
        if not os.path.isdir(mpath):
            continue
        mode_reports: Dict[str, TestReport] = {}
        for jf in sorted(glob.glob(os.path.join(mpath, "*.json"))):
            if os.path.basename(jf) == "summary.json":
                continue
            r = TestReport.load(jf)
            if r is not None:
                mode_reports[r.test] = r
        out[mode_dir] = mode_reports
    return out


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_features() -> List[Feature]:
    features: List[Feature] = []
    for readme in sorted(glob.glob(FEATURES_GLOB) + glob.glob(NESTED_FEATURES_GLOB)):
        rel = os.path.relpath(readme, ROOT)
        # feature name = directory path under features/, e.g. "webhooks" or "realtime-transcription/gmeet"
        fname = rel.replace("features/", "", 1).rsplit("/README.md", 1)[0]
        try:
            with open(readme) as f:
                text = f.read()
        except Exception:
            continue
        m = FRONTMATTER_RE.match(text)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except Exception as e:
            print(f"WARN: could not parse frontmatter of {rel}: {e}", file=sys.stderr)
            continue
        tests3 = fm.get("tests3") or {}
        gate = (tests3.get("gate") or {}).get("confidence_min", 0)
        dod_entries = tests3.get("dods") or []
        if not dod_entries:
            # Phase C: only features that have migrated get a DoD rollup. The
            # rest are silently skipped (will be surfaced in Phase D migration).
            continue
        dods: List[DoD] = []
        for entry in dod_entries:
            ev = entry.get("evidence") or {}
            dods.append(DoD(
                id=str(entry.get("id", "")),
                label=str(entry.get("label", "")),
                weight=int(entry.get("weight", 0)),
                evidence=DoDEvidence(
                    test=ev.get("test"),
                    step=ev.get("step"),
                    check=ev.get("check"),
                    modes=list(ev.get("modes") or []),
                ),
            ))
        features.append(Feature(name=fname, path=readme, gate_confidence_min=int(gate), dods=dods))
    return features


# ─────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────

def evaluate_dod(dod: DoD, reports: Dict[str, Dict[str, TestReport]]) -> None:
    """Set dod.status and dod.evidence_msgs from reports across the modes it runs in."""
    ev = dod.evidence
    target_modes = ev.modes or list(reports.keys())
    if not target_modes:
        dod.status = "missing"
        return

    statuses: List[str] = []
    for mode in target_modes:
        mode_reports = reports.get(mode, {})

        if ev.test and ev.step:
            report = mode_reports.get(ev.test)
            if not report:
                # Try smoke-* prefix (evidence may point to "webhooks" but report is "webhooks" —
                # this is the normal case; smoke tier reports are named smoke-<tier>)
                statuses.append("missing")
                dod.evidence_msgs.append((mode, f"no report for test={ev.test}"))
                continue
            step = report.steps.get(ev.step)
            if not step:
                statuses.append("missing")
                dod.evidence_msgs.append((mode, f"report has no step={ev.step}"))
                continue
            statuses.append(step.status)
            dod.evidence_msgs.append((mode, f"{ev.test}/{ev.step}: {step.message}"))

        elif ev.check:
            # Scan all smoke-* reports in this mode for a step with id == ev.check.
            found = False
            for rname, report in mode_reports.items():
                if not rname.startswith("smoke-"):
                    continue
                step = report.steps.get(ev.check)
                if step:
                    statuses.append(step.status)
                    dod.evidence_msgs.append((mode, f"{rname}/{ev.check}: {step.message}"))
                    found = True
                    break
            if not found:
                statuses.append("missing")
                dod.evidence_msgs.append((mode, f"check {ev.check} not found in any smoke-* report"))
        else:
            statuses.append("missing")
            dod.evidence_msgs.append((mode, "evidence binding invalid (needs test+step or check)"))

    # Roll up across modes: any fail → fail; any missing → missing; any skip → skip; else pass.
    if "fail" in statuses:
        dod.status = "fail"
    elif any(s == "missing" for s in statuses):
        dod.status = "missing"
    elif any(s == "skip" for s in statuses):
        dod.status = "skip"
    else:
        dod.status = "pass"


def compute_confidence(feature: Feature) -> int:
    if not feature.dods:
        return 0
    total_weight = sum(d.weight for d in feature.dods)
    if total_weight == 0:
        return 0
    # pass=full credit, skip/missing/fail=0 (user's "no legacy/no fallbacks" → strict)
    earned = sum(d.weight for d in feature.dods if d.status == "pass")
    return int(round(earned * 100.0 / total_weight))


def evaluate_feature(feature: Feature, reports: Dict[str, Dict[str, TestReport]]) -> None:
    for dod in feature.dods:
        evaluate_dod(dod, reports)
    feature.confidence = compute_confidence(feature)


# ─────────────────────────────────────────────────────────────────
# Output: markdown report
# ─────────────────────────────────────────────────────────────────

def status_glyph(s: str) -> str:
    return {"pass": "✅", "fail": "❌", "skip": "⚠️", "missing": "⬜"}.get(s, "?")


def render_markdown_report(
    tag: str,
    features: List[Feature],
    reports: Dict[str, Dict[str, TestReport]],
) -> str:
    out: List[str] = []
    out.append(f"# Release validation report — `{tag}`\n")
    out.append(f"_Generated {datetime.utcnow().isoformat()}Z from `tests3/.state/reports/`._\n")

    modes = sorted(reports.keys())
    out.append("## Deployment coverage\n")
    out.append("| Mode | Image tag | Tests run | Passed | Failed |")
    out.append("|------|-----------|-----------|--------|--------|")
    for mode in modes:
        test_reports = reports[mode]
        total = len(test_reports)
        passed = sum(1 for r in test_reports.values() if r.status == "pass")
        failed = sum(1 for r in test_reports.values() if r.status == "fail")
        img = next((r.image_tag for r in test_reports.values() if r.image_tag), "—")
        out.append(f"| `{mode}` | `{img}` | {total} | {passed} | {failed} |")
    out.append("")

    out.append("## Feature confidence\n")
    out.append("| Feature | Confidence | Gate | Status |")
    out.append("|---------|-----------:|-----:|:-------|")
    for f in sorted(features, key=lambda f: f.name):
        gate_ok = f.confidence >= f.gate_confidence_min
        gate_status = "✅ pass" if gate_ok else "❌ below gate"
        out.append(f"| `{f.name}` | **{f.confidence}%** | {f.gate_confidence_min}% | {gate_status} |")
    out.append("")

    out.append("## DoD details\n")
    for f in sorted(features, key=lambda f: f.name):
        out.append(f"### `{f.name}` ({f.confidence}% / gate {f.gate_confidence_min}%)\n")
        out.append("| # | Label | Weight | Status | Evidence |")
        out.append("|---|-------|-------:|:------:|----------|")
        for d in f.dods:
            msgs = "; ".join(f"{m}: {msg}" for m, msg in d.evidence_msgs) or "—"
            # Trim very long messages to keep the table readable.
            if len(msgs) > 200:
                msgs = msgs[:197] + "…"
            # Escape pipes for markdown
            msgs = msgs.replace("|", "\\|")
            label = d.label.replace("|", "\\|")
            out.append(f"| {d.id} | {label} | {d.weight} | {status_glyph(d.status)} {d.status} | {msgs} |")
        out.append("")

    out.append("## Raw test results\n")
    for mode in modes:
        out.append(f"### `{mode}`\n")
        out.append("| Test | Status | Duration | Steps (pass / total) |")
        out.append("|------|:------:|---------:|---------------------:|")
        for tname in sorted(reports[mode].keys()):
            r = reports[mode][tname]
            total = len(r.steps)
            passed = sum(1 for s in r.steps.values() if s.status == "pass")
            out.append(
                f"| `{tname}` | {status_glyph(r.status)} {r.status} | {r.duration_ms} ms | {passed} / {total} |"
            )
        out.append("")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────
# Output: per-mode summary JSON
# ─────────────────────────────────────────────────────────────────

def write_mode_summary(mode: str, reports: Dict[str, TestReport]) -> None:
    path = os.path.join(STATE, "reports", mode, "summary.json")
    total = len(reports)
    passed = sum(1 for r in reports.values() if r.status == "pass")
    failed = sum(1 for r in reports.values() if r.status == "fail")
    summary = {
        "mode": mode,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "tests": {
            name: {"status": r.status, "duration_ms": r.duration_ms, "step_count": len(r.steps)}
            for name, r in reports.items()
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregate tests3 reports into a release report.")
    ap.add_argument("--out", help="Path to write markdown report (default: stdout)")
    ap.add_argument("--gate-check", action="store_true",
                    help="Exit non-zero if any feature falls below its gate.confidence_min")
    ap.add_argument("--mode", action="append",
                    help="Restrict aggregation to specific mode(s); default = all modes with reports")
    ap.add_argument("--write-features", action="store_true",
                    help="[Phase D] Rewrite DoD table rows in feature README files (not implemented yet)")
    args = ap.parse_args()

    registry = load_registry()
    reports = load_reports(args.mode)
    features = load_features()

    if not reports:
        print("WARN: no reports found in tests3/.state/reports/", file=sys.stderr)

    # Per-mode summary JSON
    for mode, mreports in reports.items():
        write_mode_summary(mode, mreports)

    # Per-feature evaluation
    for f in features:
        evaluate_feature(f, reports)

    # Determine release tag
    tag_path = os.path.join(STATE, "image_tag")
    if os.path.exists(tag_path):
        with open(tag_path) as f:
            tag = f.read().strip()
    else:
        tag = "unknown"

    md = render_markdown_report(tag, features, reports)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            f.write(md)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(md)

    if args.write_features:
        for f in features:
            write_feature_readme(f, tag)

    if args.gate_check:
        below = [f for f in features if f.confidence < f.gate_confidence_min]
        if below:
            print(f"GATE FAILED: {len(below)} feature(s) below confidence_min:", file=sys.stderr)
            for f in below:
                print(f"  - {f.name}: {f.confidence}% < {f.gate_confidence_min}%", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
