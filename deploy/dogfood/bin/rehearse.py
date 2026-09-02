#!/usr/bin/env python3
"""rehearse — enter a user state on the running stack, or wipe one subject out of it.

PRD decision 38. The states live in `deploy/dogfood/rehearse/states.yaml`; this is the hand tool
in front of them, and the control MCP's `rehearse` / `subject_reset` tools call the same functions.

    rehearse.py states                                   # what the catalogue holds
    rehearse.py plan organizer-invited olga@rehearse.test # resolve every step, execute none
    rehearse.py enter organizer-invited olga@rehearse.test
    rehearse.py enter attendee-stranger-minutes sam@rehearse.test --meeting 2026-03-16 --fresh
    rehearse.py enter reply-pending sam@rehearse.test --runner openai-agent   # on the CCC Qwen
    rehearse.py reset olga@rehearse.test
    rehearse.py all                                      # the whole catalogue (run_all)

Every address must be under $VEXA_REHEARSE_DOMAIN (default rehearse.test), and the tool refuses
while a live meeting belongs to anyone outside it. Both refusals happen before the first door.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from rehearse import catalogue as cat                                    # noqa: E402
from rehearse.doors import MAIL_ADDR, DoorRefused, LiveDoors            # noqa: E402
from rehearse.engine import DEFAULT_MEETING, DEFAULT_WHEN, Refused       # noqa: E402
from rehearse.engine import rehearse as enter_state                      # noqa: E402
from rehearse.engine import subject_reset                                # noqa: E402


def cmd_states(a, catalog) -> int:
    for name, st in catalog.states.items():
        print(f"\n{name}\n  {' '.join(st.summary.split())}")
        print(f"  story:  {st.story}")
        for step in st.steps:
            print(f"    {step.index:>2}. {step.do:<24} via {step.door}")
        print(f"  verify: {', '.join(v['check'] for v in st.verify)}")
    print(f"\ndomain: @{catalog.domain()}   fixtures: {catalog.fixtures_dir()}   "
          f"mailbox: {MAIL_ADDR}")
    return 0


def cmd_plan(a, catalog) -> int:
    res = enter_state(a.state, a.subject, meeting=a.meeting, when=a.when, doors=_doors(a),
                      catalog=catalog, mailbox=MAIL_ADDR, dry_run=True, runner=a.runner)
    print(json.dumps(res.to_dict(), indent=1, default=str))
    return 0


def cmd_enter(a, catalog) -> int:
    res = enter_state(a.state, a.subject, meeting=a.meeting, when=a.when, doors=_doors(a),
                      catalog=catalog, mailbox=MAIL_ADDR, fresh=a.fresh, runner=a.runner)
    if a.json:
        print(json.dumps(res.to_dict(), indent=1, default=str))
    else:
        print(f"{res.state} as {res.subject} — {'OK' if res.ok else 'FAILED'} in {res.wall_s:.1f}s")
        for step in res.steps:
            print(f"  {'ok ' if step.get('ok') else 'STOP'} {step['do']:<24} {step.get('why', '')}")
        for name, url in res.links.items():
            print(f"  link  {name}: {url}")
        for v in res.verify:
            print(f"  {'pass' if v['ok'] else 'FAIL'}  {v['check']:<20} {v['detail']}")
        if res.error:
            print(f"\n  {res.error}")
    return 0 if res.ok else 1


def cmd_reset(a, catalog) -> int:
    out = subject_reset(a.subject, doors=_doors(a), catalog=catalog)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["ok"] else 1


def cmd_all(a, catalog) -> int:
    from rehearse import run_all
    argv = ["--meeting", a.meeting, "--when", a.when]
    if a.only:
        argv += ["--only", a.only]
    if a.json:
        argv.append("--json")
    if a.stub:
        argv.append("--stub")
    return run_all.main(argv)


def _doors(a):
    if getattr(a, "stub", False):
        from rehearse.stub_doors import StubDoors
        return StubDoors()
    return LiveDoors()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rehearse.py", description=__doc__.split("\n")[0])
    ap.add_argument("--stub", action="store_true",
                    help="run against the offline door stub — proves the recipe, touches nothing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("states", help="list the catalogue").set_defaults(fn=cmd_states)

    for name, fn in (("plan", cmd_plan), ("enter", cmd_enter)):
        p = sub.add_parser(name, help=f"{name} a state")
        p.add_argument("state")
        p.add_argument("subject", help="the address to be, under the test domain")
        p.add_argument("--meeting", default=DEFAULT_MEETING, help="a DNA fixture date")
        p.add_argument("--when", default=DEFAULT_WHEN, help="+30m · +3h · an epoch · ISO-8601")
        p.add_argument("--json", action="store_true")
        p.add_argument("--fresh", action="store_true",
                       help="reset the subject and the derived organizer first (DELETES)")
        p.add_argument("--runner", default="",
                       help="pin this recipe's subjects to a harness (claude-code | openai-agent)")
        p.set_defaults(fn=fn)

    p = sub.add_parser("reset", help="remove one subject entirely")
    p.add_argument("subject")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("all", help="run every state (run_all)")
    p.add_argument("--only", default="")
    p.add_argument("--meeting", default=DEFAULT_MEETING)
    p.add_argument("--when", default=DEFAULT_WHEN)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_all)

    a = ap.parse_args(argv)
    try:
        return a.fn(a, cat.load())
    except (Refused, DoorRefused) as e:
        # A refusal is the product, not a crash: it names what is in the way and what to do.
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except cat.CatalogueError as e:
        print(f"catalogue: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
