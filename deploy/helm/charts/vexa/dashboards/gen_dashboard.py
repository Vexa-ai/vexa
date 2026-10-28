#!/usr/bin/env python3
"""GENERATE adoption-panel.json from sql/*.sql plus the layout declared below.

The same split `core/flows/schema.sql` uses, for the same reason: the thing a human reviews
(SQL, in a file with syntax highlighting and comments) is not the thing a machine consumes
(one line of JSON-escaped text inside a Grafana panel). Editing the JSON by hand is the defect
this script exists to prevent — a drift check in
`core/identity/services/admin-api/tests/test_stack_adoption_panel.py::test_dashboard_json_matches_sql_files`
fails if the two disagree, so a hand-edit is caught rather than shipped.

    python3 deploy/helm/charts/vexa/dashboards/gen_dashboard.py          # rewrite the JSON
    python3 deploy/helm/charts/vexa/dashboards/gen_dashboard.py --check  # exit 1 on drift

Stdlib only, no Grafana SDK: this runs in CI on a tree with nothing installed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SQL = HERE / "sql"
OUT = HERE / "adoption-panel.json"

APP_DS = {"type": "grafana-postgresql-datasource", "uid": "vexa-app-db"}
FLOWS_DS = {"type": "grafana-postgresql-datasource", "uid": "vexa-flows-db"}

# (file stem, title, datasource, panel type, grid, options) — the layout, declared once.
# `unit` "percent" renders 0-100 with a % suffix; None leaves Grafana's default.
PANELS = [
    ("second-invite-rate", "Second-invite rate by a non-organizer", FLOWS_DS, "stat",
     dict(x=0, y=0, w=8, h=5), dict(unit="percent", decimals=1)),
    ("active-users", "Active users", FLOWS_DS, "stat",
     dict(x=8, y=0, w=4, h=5), dict(unit="none")),
    ("meetings-captured", "Meetings captured", FLOWS_DS, "stat",
     dict(x=12, y=0, w=4, h=5), dict(unit="none")),
    ("teams-covered", "Teams covered", FLOWS_DS, "stat",
     dict(x=16, y=0, w=4, h=5), dict(unit="none")),
    ("roster-coverage", "Invites carrying an attendee roster", FLOWS_DS, "stat",
     dict(x=20, y=0, w=4, h=5), dict(unit="percent", decimals=1)),

    ("second-invites-per-week", "Second invites per week", FLOWS_DS, "timeseries",
     dict(x=0, y=5, w=12, h=8), dict(unit="none")),
    ("people-per-week", "Distinct people in captured meetings, per week", FLOWS_DS,
     "timeseries", dict(x=12, y=5, w=12, h=8), dict(unit="none")),

    ("second-invite-cohort", "Second-invite cohort", FLOWS_DS, "table",
     dict(x=0, y=13, w=12, h=6), dict()),
    ("coverage-by-team", "Coverage by team", FLOWS_DS, "table",
     dict(x=12, y=13, w=12, h=6), dict()),

    ("active-users-by-month", "Active users by calendar month (usage report)", FLOWS_DS,
     "table", dict(x=0, y=19, w=12, h=8), dict()),
    ("users-platform", "Accounts on the platform", APP_DS, "table",
     dict(x=12, y=19, w=6, h=8), dict()),
    ("meetings-platform", "Meetings completed (platform cross-check)", APP_DS, "stat",
     dict(x=18, y=19, w=6, h=8), dict(unit="none")),
]

# The first block of `--` comments at the top of each .sql file becomes the panel description,
# so the definition a reader needs is IN the panel, not only in the README. Grafana renders it
# as markdown behind the panel's ⓘ.
def description_of(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if not line.startswith("--"):
            break
        lines.append(line[2:].lstrip())
    return "\n".join(lines).strip()


def build() -> dict:
    panels = []
    for pid, (stem, title, ds, ptype, grid, opts) in enumerate(PANELS, start=1):
        sql = (SQL / f"{stem}.sql").read_text()
        panel = {
            "id": pid,
            "type": ptype,
            "title": title,
            "description": description_of(sql),
            "datasource": ds,
            "gridPos": {"x": grid["x"], "y": grid["y"], "w": grid["w"], "h": grid["h"]},
            "fieldConfig": {
                "defaults": {k: v for k, v in opts.items()},
                "overrides": [],
            },
            "targets": [{
                "refId": "A",
                "datasource": ds,
                "editorMode": "code",
                "format": "time_series" if ptype == "timeseries" else "table",
                "rawQuery": True,
                "rawSql": sql,
            }],
        }
        if ptype == "stat":
            panel["options"] = {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "textMode": "auto", "colorMode": "value", "graphMode": "none",
                "justifyMode": "auto",
            }
        panels.append(panel)

    return {
        "uid": "vexa-adoption-panel",
        "title": "Vexa — Adoption",
        "description": (
            "Who inside this organisation is actually using Vexa, and whether it is spreading "
            "without us. Every number is computed inside your own cluster from your own two "
            "Postgres databases; nothing leaves it. Definitions, and the one that is still "
            "provisional, are in deploy/helm/charts/vexa/dashboards/README.md."
        ),
        "tags": ["vexa", "adoption"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "graphTooltip": 0,
        "refresh": "",
        # The panels carry their own windows via $window_days; the picker is left wide so a
        # reader who narrows it does not silently narrow half the dashboard and not the rest.
        "time": {"from": "now-90d", "to": "now"},
        "templating": {"list": [
            {
                "name": "window_days", "type": "textbox", "label": "Trailing window (days)",
                "query": "30", "current": {"text": "30", "value": "30"},
                "options": [], "hide": 0,
                "description": "The trailing window every windowed panel uses. 30 is the "
                               "provisional default for 'active user' (PRD §16.5).",
            },
            {
                "name": "min_meetings", "type": "textbox",
                "label": "Captured meetings to count as active",
                "query": "1", "current": {"text": "1", "value": "1"},
                "options": [], "hide": 0,
                "description": "How many captured meetings a person must be present in, inside "
                               "the window, to count as active. Provisional default 1.",
            },
        ]},
        "panels": panels,
    }


def main() -> int:
    rendered = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != rendered:
            print(f"DRIFT: {OUT} does not match sql/ + the layout in {Path(__file__).name}.\n"
                  f"       Re-run: python3 {Path(__file__)}", file=sys.stderr)
            return 1
        print(f"  ✓ {OUT.name} matches sql/ ({len(PANELS)} panels)")
        return 0
    OUT.write_text(rendered)
    print(f"wrote {OUT} ({len(PANELS)} panels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
