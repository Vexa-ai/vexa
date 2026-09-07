#!/usr/bin/env python
"""Offline replay: do links between a desk and a group survive the group being RENAMED?

PRD decision 26's proof obligation — *"the DNA replay across three meetings with links between the
desk and the group holding after a rename"*. This is that replay with the cost taken out of it: no
stack, no docker, no mailpit, no model. Two real DNA fixtures, two scratch workspaces, the same
`shared/entities.upsert_entity` the agent calls and the same `control_plane/link_resolver` the panel
calls. It runs in under a second and it can run in CI.

WHAT IT PROVES, and why the shape is what it is:

  1. A desk and a group both exist, both get ids at creation, and the desk's notes link into the
     group's people — written by `entity_upsert`, in the `ws:` form, without the agent being asked.
  2. Between the two meetings the group is RENAMED *and its directory is moved* — the harder half.
     A rename alone only tests the display name; moving the tree is what breaks a link written
     against a slug, which is what links were written against before this decision.
  3. Every link written before the rename still resolves after it, to the same canonical URL.
  4. A reader who is not in the group gets `not-yours` for exactly those links — with a title, no
     error, and no way to open the page. *"If a workspace is not available, it's okay — by design."*
  5. The desk README's `## Workspaces` link to the group resolves after the rename too.
  6. A DESK, though, is a different answer (founder ruling, 2026-09-02): readable by any signed-in
     member of this instance, writable only by its owner, and `not-yours` only from outside the
     instance. Groups gate on membership; desks gate on writing.
  7. The README's `## Now` is built from FILED DATES and nothing else (coordinator ruling,
     2026-09-02). The replay files them the way the write-back phase does — `held_at` when a
     meeting ran, `report_delivered_at` when its write-up went out, `scheduled_at` for the next
     one, `due_at` for a commitment — and then asserts that all three of `Now`'s lists say what
     those fields say. A page here also carries a date in PROSE, under the heading the old scraper
     matched, and `Now` must not show it.

Usage:  python core/agent/eval/workspace_links_replay.py --fixtures ~/dna-fixtures
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))          # core/agent — the same path pytest uses
sys.path.insert(0, str(HERE.parents[2]))          # core — for workspaces.shared (PRD decision 47)

from control_plane import link_resolver, workspace_ids as ids  # noqa: E402
from shared import desk_readme  # noqa: E402
from workspaces.shared.entities import upsert_entity  # noqa: E402
from workspaces.shared.links import cross_workspace_refs  # noqa: E402

DESK = "126"                              # the shape the live instance actually has
GROUP = "aswf-dna-project-b7b2ee"
COLLEAGUE = "999"                         # signed in to this instance, in none of its groups
OUTSIDE = ""                              # no subject at all — outside the instance entirely

_PAREN = re.compile(r"\s*\(([^)]*)\)\s*$")
_LIST = re.compile(r'^present:\s*\[(.*)\]\s*$', re.M)
_DATE = re.compile(r"^date:\s*(\S+)\s*$", re.M)
_BULLET = re.compile(r'^\s+-\s+"(.*)"\s*$', re.M)
_SECTION = re.compile(r"^(decided|committed|open):\s*$", re.M)


def read_truth(path: pathlib.Path) -> dict:
    """The fixture's own sidecar, read with a regex rather than PyYAML.

    Deliberate: `core/agent` declares no yaml dependency for its tests, and a proof that needs a new
    dependency to run is a proof that stops being run."""
    raw = path.read_text(encoding="utf-8")
    people, orgs = [], []
    m = _LIST.search(raw)
    if m:
        for item in re.findall(r'"([^"]+)"', m.group(1)):
            name = _PAREN.sub("", item).strip()
            org = (_PAREN.search(item).group(1).strip() if _PAREN.search(item) else "")
            if name:
                people.append(name)
            if org and org not in orgs:
                orgs.append(org)
    sections: dict[str, list[str]] = {}
    marks = [(mm.group(1), mm.start(), mm.end()) for mm in _SECTION.finditer(raw)]
    for i, (name, _s, e) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(raw)
        sections[name] = [b for b in _BULLET.findall(raw[e:end])]
    date = _DATE.search(raw).group(1) if _DATE.search(raw) else path.stem
    return {"date": date, "people": people, "orgs": orgs, **sections}


def git(ws: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)


def make_workspace(root: pathlib.Path, slug: str, *, members: list[str] | None = None) -> pathlib.Path:
    ws = root / slug
    (ws / "kg" / "entities").mkdir(parents=True)
    if members is not None:
        (ws / "policy").mkdir(parents=True)
        (ws / "policy" / "members.json").write_text(json.dumps(
            [{"subject": s, "role": "owner" if i == 0 else "contributor"}
             for i, s in enumerate(members)]))
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "t@t")
    git(ws, "config", "user.name", "t")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "seed", "--allow-empty")
    return ws


def member_check(root, slug, subject):
    """The authoritative roster read, exactly as the API injects it."""
    try:
        rows = json.loads((pathlib.Path(root) / slug / "policy" / "members.json").read_text())
    except (OSError, ValueError):
        return None
    return next((r.get("role") for r in rows if r.get("subject") == subject), None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default="~/dna-fixtures")
    ap.add_argument("--json", action="store_true", help="machine-readable output only")
    args = ap.parse_args()

    fx = pathlib.Path(args.fixtures).expanduser()
    truths = sorted(fx.glob("*.truth.yaml"))[:2]
    if len(truths) < 2:
        print(f"need 2 fixtures in {fx}; found {len(truths)}", file=sys.stderr)
        return 2

    root = pathlib.Path(tempfile.mkdtemp(prefix="wsids-replay-"))
    report: dict = {"fixtures": [t.name for t in truths], "root": str(root), "steps": []}
    try:
        desk = make_workspace(root, DESK)
        group = make_workspace(root, GROUP, members=[DESK])
        registry = ids.WorkspaceRegistry()
        migrated = ids.migrate(root, registry)
        ids.rename(registry, registry.by_slug(GROUP)["id"], "ASWF DNA Project")
        group_id = registry.by_slug(GROUP)["id"]
        desk_id = registry.by_slug(DESK)["id"]
        report["ids"] = {"desk": desk_id, "group": group_id,
                         "minted_at_migration": len(migrated["minted"])}

        mounts = [{"path": str(desk)}, {"path": str(group)}]
        written = 0

        def at(day: str, hour: int = 15) -> float:
            """An instant on a fixture's day, UTC — what `entity_upsert(dates=)` files."""
            d = datetime.date.fromisoformat(day)
            return datetime.datetime(d.year, d.month, d.day, hour,
                                     tzinfo=datetime.timezone.utc).timestamp()

        def play(truth: dict, *, delivered: bool) -> None:
            """One meeting, the way decision 22 says a group meeting lands: the PEOPLE and the
            COMPANIES go on the GROUP desk (the run actively maintains it), and the person's own
            desk gets the meeting entity — whose links therefore point across."""
            nonlocal written
            for person in truth["people"]:
                upsert_entity(group, "person", person,
                              [f"Attended the DNA TSC meeting on {truth['date']}."],
                              f"the {truth['date']} transcript", today=truth["date"])
            for org in truth["orgs"]:
                upsert_entity(group, "company", org,
                              [f"Represented at the DNA TSC on {truth['date']}."],
                              f"the {truth['date']} transcript", today=truth["date"])
            facts = [f"Present: " + ", ".join(f"[[{p}]]" for p in truth["people"]) + "."]
            facts += [f"Decided: {d}" for d in truth.get("decided", [])[:3]]
            # THE DATES ARE FILED, NOT WRITTEN. `held_at` says it ran; `report_delivered_at`
            # says the write-up reached them, which is what closes the open commitment. A meeting
            # with the first and not the second is exactly what `Now` calls open.
            stamps = {"held_at": at(truth["date"])}
            if delivered:
                stamps["report_delivered_at"] = at(truth["date"], 18)
            out = upsert_entity(desk, "meeting", f"DNA TSC {truth['date']}", facts,
                                f"the {truth['date']} transcript", today=truth["date"],
                                mounts=mounts, dates=stamps)
            # …and one date in PROSE, under the heading the retired scraper matched. `Now` must not
            # show it: nothing filed it, so nothing could ever move it or close it either.
            page = desk / out["path"]
            page.write_text(page.read_text()
                            + f"\n## Committed\n\n- Circulate the charter by {truth['date']}\n")
            written += len(out.get("links_rewritten") or [])
            report["steps"].append({"meeting": truth["date"], "page": out["path"],
                                    "links_rewritten": len(out.get("links_rewritten") or [])})

        first, second = read_truth(truths[0]), read_truth(truths[1])
        play(first, delivered=True)

        # ── THE RENAME, and the move with it ────────────────────────────────────────────────────
        renamed_slug = "digital-naming-authority"
        shutil.move(str(group), str(root / renamed_slug))
        group = root / renamed_slug
        mounts = [{"path": str(desk)}, {"path": str(group)}]
        ids.sync_workspace(root, renamed_slug, registry=registry)
        ids.rename(registry, group_id, "Digital Naming Authority")
        report["rename"] = {"from": GROUP, "to": renamed_slug,
                            "id_unchanged": registry.by_slug(renamed_slug)["id"] == group_id}

        play(second, delivered=False)          # its write-up never went out — an OPEN commitment

        # The two facts a desk holds about what is ahead, both FILED: the next meeting, and one
        # thing owed by a date.
        when_next = at(second["date"]) + 14 * 86400
        upsert_entity(desk, "meeting", "DNA TSC next", ["Booked in the series."], "the invite",
                      today=second["date"], dates={"scheduled_at": when_next})
        upsert_entity(desk, "decision", "Sign the ASWF CLA",
                      ["SPI asked for the standard shape rather than an authorisation letter."],
                      f"the {second['date']} transcript", today=second["date"],
                      dates={"due_at": at(second["date"]) + 7 * 86400})

        # ── the desk README, which is the desk: a HUB OF LINKS (founder refinement 2026-09-02) ──
        # Fed every mount, so the group's own cards appear on the desk in `ws:` id form — the whole
        # of "mostly links to the other cards in different workspaces".
        desk_readme.update_readme(
            desk, mounts=[{"path": str(desk), "id": desk_id}, {"path": str(group), "id": group_id}],
            workspaces=[{"id": group_id, "name": "Digital Naming Authority"}],
            touches=[{"workspace": group_id,
                      "path": "kg/entities/person/cottalango-leon.md", "at": 9e9}],
            home_id=desk_id, name="olga@spi.com", now=at(second["date"], 18))
        readme = (desk / desk_readme.README).read_text()
        now_block = readme.split("## Now", 1)[1].split("<!-- desk:now:end -->", 1)[0]
        report["readme"] = {
            "links_to_group_cards": readme.count(f"[[ws:{group_id}/"),
            "pinned_is_untouched": desk_readme.PINNED_HINT in readme,
            "most_used_card_is_first": (readme.split("## People")[1].strip().splitlines() or [""])[0]
                                       == f"- [[ws:{group_id}/cottalango-leon]]",
        }
        report["now"] = {
            "next_meeting": "[[DNA TSC next]]" in now_block,
            "dated_commitment": "[[Sign the ASWF CLA]]" in now_block,
            "open_commitment": (f"[[DNA TSC {second['date']}]]" in now_block
                                and "no write-up yet" in now_block),
            "delivered_meeting_is_closed": f"[[DNA TSC {first['date']}]]" not in now_block,
            "a_date_in_prose_is_ignored": "Circulate the charter" not in now_block,
            "lines": [ln for ln in now_block.strip().splitlines() if ln.startswith("- ")],
        }

        # ── RESOLVE EVERYTHING THE DESK NOW HOLDS, as both readers ──────────────────────────────
        refs: list[str] = []
        for page in sorted((desk / "kg").rglob("*.md")) + [desk / desk_readme.README]:
            refs += [r.raw for r in cross_workspace_refs(page.read_text(encoding="utf-8"))]
        refs = list(dict.fromkeys(refs))

        def tally(subject: str) -> dict:
            out = link_resolver.resolve_many(refs, subject=subject, root=root, registry=registry,
                                             here=registry.by_slug(DESK), is_member=member_check)
            counts = {"readable": 0, "not-yours": 0, "gone": 0}
            for r in out:
                counts[r["access"]] += 1
            return {"counts": counts, "sample": out[:2]}


        owner, colleague = tally(DESK), tally(COLLEAGUE)
        report["links"] = {"written_in_id_form": written, "distinct_refs": len(refs),
                           "as_the_desk_owner": owner["counts"],
                           "as_a_non_member_of_the_group": colleague["counts"]}
        report["readme_links_to_the_group"] = f"[[ws:{group_id}/README.md]]" in readme

        # ── the DESK, read the other way round: a link INTO it, seen by three readers ───────────
        desk_ref = f"ws:{desk_id}/kg/entities/meeting/dna-tsc-{first['date']}.md"

        def one(subject: str) -> dict:
            r = link_resolver.resolve(desk_ref, subject=subject, root=root, registry=registry,
                                      is_member=member_check)
            return {"access": r["access"], "writable": r["writable"]}

        report["a_link_into_the_desk"] = {"its_owner": one(DESK), "a_colleague": one(COLLEAGUE),
                                          "outside_the_instance": one(OUTSIDE)}

        # ── the assertions this replay exists to make ───────────────────────────────────────────
        checks = {
            "the group kept its id across the rename and the move": report["rename"]["id_unchanged"],
            "the desk wrote cross-workspace links without being asked": written > 0,
            "every link still resolves for the owner after the rename":
                owner["counts"]["readable"] == len(refs) and len(refs) > 0,
            "no link is broken (`gone`) for anybody": owner["counts"]["gone"] == 0
                                                      and colleague["counts"]["gone"] == 0,
            "a non-member gets not-yours for every one of them, and never an error":
                colleague["counts"]["not-yours"] == len(refs),
            "the desk README links to the group by id": report["readme_links_to_the_group"],
            "the desk README is a hub of links into the group's cards":
                report["readme"]["links_to_group_cards"] > 1,
            "the card the person opened is at the top of its section":
                report["readme"]["most_used_card_is_first"],
            "Pinned is left to the person": report["readme"]["pinned_is_untouched"],
            "`Now` shows the next meeting, from `scheduled_at`": report["now"]["next_meeting"],
            "`Now` shows a commitment, from `due_at`": report["now"]["dated_commitment"],
            "`Now` shows the meeting whose write-up never went out":
                report["now"]["open_commitment"],
            "`Now` drops the meeting whose write-up did":
                report["now"]["delivered_meeting_is_closed"],
            "`Now` ignores a date written in prose": report["now"]["a_date_in_prose_is_ignored"],
            "the desk is readable by its owner, and writable":
                report["a_link_into_the_desk"]["its_owner"] == {"access": "readable", "writable": True},
            "the desk is readable by a colleague, and NOT writable":
                report["a_link_into_the_desk"]["a_colleague"] == {"access": "readable", "writable": False},
            "the desk is not-yours only from outside the instance":
                report["a_link_into_the_desk"]["outside_the_instance"] == {"access": "not-yours",
                                                                          "writable": False},
        }
        report["checks"] = checks
        report["ok"] = all(checks.values())

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(report, indent=2))
            print()
            for name, ok in checks.items():
                print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        return 0 if report["ok"] else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
