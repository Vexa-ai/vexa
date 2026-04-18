#!/usr/bin/env python3
"""
tests3 stage state machine — primitive #5 of release-system-review §2.6.

One file holds the current stage (`tests3/.current-stage`). One log holds
history (`tests3/.state/stage-log.ndjson`). Every Makefile target and every
AI skill guards on stage before acting; illegal transitions hard-fail.

Usage:
  python3 tests3/lib/stage.py current                   # print current stage as YAML
  python3 tests3/lib/stage.py assert-is <name>          # exit 1 if not in <name>
  python3 tests3/lib/stage.py enter <name> [--actor X]  # transition (validate + log)
  python3 tests3/lib/stage.py objectives <name>         # print stage file contents
  python3 tests3/lib/stage.py next                      # print canonical next stage(s)
  python3 tests3/lib/stage.py probe                     # human-readable status (`make stage`)

Library use:
  import stage; s = stage.current()
  stage.assert_is("validate")
  stage.enter("triage", actor="AI:triage", reason="red")
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("ERROR: needs PyYAML", file=sys.stderr); sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent.parent
T3 = ROOT / "tests3"
CURRENT = T3 / ".current-stage"
LOG = T3 / ".state" / "stage-log.ndjson"
STAGES_DIR = T3 / "stages"

# Canonical state machine (§5.5). For each stage, the set of legal predecessors.
# "idle" is reachable from teardown or a fresh repo. Develop can be entered from
# plan (first time) or triage (after a failure).
TRANSITIONS = {
    "idle":      {"teardown", None},          # None = uninitialised repo
    "groom":     {"idle"},
    "plan":      {"groom"},
    "develop":   {"plan", "triage"},
    "provision": {"develop"},                 # first-time provision
    "deploy":    {"provision", "develop"},    # post-develop, post-provision
    "validate":  {"deploy"},
    "triage":    {"validate", "human"},       # validate(red) or human(gap)
    "human":     {"validate"},                # validate(green)
    "ship":      {"human"},
    "teardown":  {"ship"},
}

STAGE_ORDER = [
    "idle", "groom", "plan", "develop", "provision", "deploy",
    "validate", "triage", "human", "ship", "teardown",
]


# ─────────────────────────────────────────────────────────────────
# Read / write
# ─────────────────────────────────────────────────────────────────

def current() -> dict:
    if not CURRENT.is_file():
        return {"release_id": None, "stage": None, "entered_at": None, "last_action": None}
    return yaml.safe_load(CURRENT.read_text()) or {}


def _write_current(data: dict) -> None:
    CURRENT.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))


def _append_log(evt: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(evt) + "\n")


# ─────────────────────────────────────────────────────────────────
# Assertions
# ─────────────────────────────────────────────────────────────────

class StageError(RuntimeError):
    pass


def assert_is(expected: str) -> None:
    s = current()
    if s.get("stage") != expected:
        raise StageError(
            f"expected stage '{expected}', current stage is '{s.get('stage')}'."
            f" Transition via `make release-<X>` or `stage.py enter <X>`."
        )


def enter(name: str, actor: str = "make", reason: Optional[str] = None,
          release_id: Optional[str] = None) -> None:
    if name not in TRANSITIONS:
        raise StageError(f"unknown stage '{name}' (known: {list(TRANSITIONS)})")
    s = current()
    cur = s.get("stage")
    if cur not in TRANSITIONS[name]:
        raise StageError(
            f"illegal transition '{cur}' → '{name}'. Legal predecessors of "
            f"'{name}': {sorted(TRANSITIONS[name] - {None})}"
        )
    now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    new = {
        "release_id": release_id or s.get("release_id"),
        "stage": name,
        "entered_at": now,
        "last_action": f"stage.py enter {name}",
    }
    _write_current(new)
    evt = {
        "t": now,
        "release": new["release_id"],
        "from": cur,
        "to": name,
        "actor": actor,
    }
    if reason:
        evt["reason"] = reason
    _append_log(evt)


def objectives(name: str) -> str:
    p = STAGES_DIR / f"{STAGE_ORDER.index(name):02d}-{name}.md"
    if not p.is_file():
        raise StageError(f"no stage file at {p.relative_to(ROOT)}")
    return p.read_text()


def next_stages(name: Optional[str] = None) -> list[str]:
    name = name or (current().get("stage") or "idle")
    out = [s for s, preds in TRANSITIONS.items() if name in preds]
    return out


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def _cmd_current(_):
    print(yaml.safe_dump(current(), default_flow_style=False, sort_keys=False).rstrip())
    return 0


def _cmd_assert(args):
    try:
        assert_is(args.stage)
    except StageError as e:
        print(f"stage-assert FAILED: {e}", file=sys.stderr); return 1
    return 0


def _cmd_enter(args):
    try:
        enter(args.stage, actor=args.actor, reason=args.reason, release_id=args.release)
    except StageError as e:
        print(f"stage-enter FAILED: {e}", file=sys.stderr); return 1
    print(f"  stage: {args.stage}", file=sys.stderr)
    return 0


def _cmd_objectives(args):
    try:
        print(objectives(args.stage))
    except StageError as e:
        print(e, file=sys.stderr); return 1
    return 0


def _cmd_next(_):
    for s in next_stages():
        print(s)
    return 0


def _cmd_probe(_):
    s = current()
    stage = s.get("stage") or "uninitialised"
    print(f"release:   {s.get('release_id') or '-'}")
    print(f"stage:     {stage}")
    if s.get("entered_at"):
        print(f"entered:   {s['entered_at']}")
    nxt = next_stages(stage)
    if nxt:
        print(f"next:      {' | '.join(nxt)}")
    try:
        obj = objectives(stage)
        # Extract the first numbered "Objective" line for a terse probe
        for line in obj.splitlines():
            if line.startswith("Objective"):
                print(f"objective: {line.split(None, 1)[1].strip()}")
                break
    except StageError:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("current", help="print .current-stage").set_defaults(fn=_cmd_current)

    p = sub.add_parser("assert-is", help="exit 1 if not in <stage>")
    p.add_argument("stage", choices=STAGE_ORDER)
    p.set_defaults(fn=_cmd_assert)

    p = sub.add_parser("enter", help="transition to <stage>")
    p.add_argument("stage", choices=STAGE_ORDER)
    p.add_argument("--actor", default="make")
    p.add_argument("--reason")
    p.add_argument("--release")
    p.set_defaults(fn=_cmd_enter)

    p = sub.add_parser("objectives", help="print stage file contents")
    p.add_argument("stage", choices=STAGE_ORDER)
    p.set_defaults(fn=_cmd_objectives)

    sub.add_parser("next", help="print legal next stage(s)").set_defaults(fn=_cmd_next)
    sub.add_parser("probe", help="human-readable status").set_defaults(fn=_cmd_probe)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
