#!/usr/bin/env python3
"""
Step 3 of §4.3 — collapse two registries into one.

Inputs:
  tests3/checks/registry.json   — 81 atomic checks across 4 tiers (static/env/health/contract)
  tests3/test-registry.yaml     — 18 script-based test entries

Output:
  tests3/registry.yaml          — single file, `type:` discriminator
                                  (grep | http | env | script)

Schema per §3.3:
  <id>:
    type:       grep | http | env | script
    ...type-specific fields...
    modes:      [lite, compose, helm]  (default: all three unless skip_modes)
    state:      stateful | stateless   (default: stateless)
    mutates:    []                     (declared only if state=stateful)
    max_duration_sec: int              (optional)

Type mappings:
  static  → grep           (file + must_match/must_not_match)
  env     → env            (env_checks)
  health  → http           (url + expect_code)  OR  script (url is a special method name)
  contract→ http  OR script (same disambiguation)
  (entries in test-registry.yaml) → script (script: path)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: needs PyYAML", file=sys.stderr); sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent.parent
T3 = ROOT / "tests3"

ALL_MODES = ["lite", "compose", "helm"]

# Health/contract `url` values that are NOT real URLs — they're dispatch names
# that resolve to Python handlers in checks/run. These become type: script with
# method: <SPECIAL>. Kept verbatim for now; step 4 (aggregate rewrite) can
# decide whether to convert them into dedicated test scripts.
SPECIAL_DISPATCHES = {
    "DB_SCHEMA_CHECK", "DB_USERS_CHECK", "DB_TOKEN_SCOPES_CHECK",
    "REDIS_PING", "RUNTIME_API_HEALTH", "TRANSCRIPTION_HEALTH",
    "DASHBOARD_WS_URL",
    "ADMIN_API_DB_EXISTS_CHECK", "POSTGRES_NO_DISK_WARNING_CHECK",
    "DASHBOARD_MEETINGS_NOT_ALL_FAILED_CHECK",
    "DASHBOARD_WEBHOOKS_ALL_EVENT_TYPES_CHECK",
}
SPECIAL_METHODS = {
    "WS_PING", "TRANSCRIPTION_TEST", "BOT_STATUS_CHECK",
    "BROWSER_SESSION_CDP_CHECK", "BOT_RECORDING_CHECK",
    "DB_POOL_CHECK", "MINIO_WRITABLE", "SEGMENT_PIPELINE",
    "CORS_PREFLIGHT",
}


def _modes(entry: dict) -> list:
    skip = set(entry.get("skip_modes") or [])
    return [m for m in ALL_MODES if m not in skip]


def migrate_check(c: dict) -> dict:
    """One entry from checks/registry.json['locks'] → new schema."""
    tier = c.get("tier") or ""
    out = {}

    # Preserve ancestry — symptom/proves/found are human-readable context
    for k in ("proves", "symptom", "found"):
        if c.get(k):
            out[k] = c[k]

    if tier == "static":
        # Most static checks are grep-on-a-file. A handful (HELM_CHART_VALID,
        # HELM_TEMPLATE_RENDERS) are `url:`-dispatched specials that shell out
        # to helm lint / helm template — they're type: script with dispatch.
        if "file" in c:
            out["type"] = "grep"
            out["file"] = c["file"]
            if "must_match" in c: out["must_match"] = c["must_match"]
            if "must_not_match" in c: out["must_not_match"] = c["must_not_match"]
        elif "url" in c:
            out["type"] = "script"
            out["dispatch"] = c["url"]
            out["from_tier"] = "static"
        else:
            out["type"] = "unknown"
            out["_orig"] = c
        if "only_branches" in c: out["only_branches"] = c["only_branches"]
        out["modes"] = list(ALL_MODES)
        out["state"] = "stateless"
        out["max_duration_sec"] = 5
        return out

    if tier == "env":
        out["type"] = "env"
        out["env_checks"] = c["env_checks"]
        out["modes"] = _modes(c)
        out["state"] = "stateless"
        out["max_duration_sec"] = 10
        return out

    if tier in ("health", "contract"):
        url = c.get("url") or ""
        method = c.get("method") or ""
        # Special dispatch names → type: script
        if url in SPECIAL_DISPATCHES or method in SPECIAL_METHODS:
            out["type"] = "script"
            out["dispatch"] = url or method  # which Python handler in checks/run
            out["from_tier"] = tier           # aggregate.py can still bucket if needed
        else:
            out["type"] = "http"
            # URL keeps its $GATEWAY_URL / $ADMIN_URL / $DASHBOARD_URL placeholders
            out["url"] = url
            if method:
                out["method"] = method
            if "expect_code" in c:
                out["expect_code"] = c["expect_code"]
            if "auth" in c:
                out["auth"] = c["auth"]
            if "data" in c:
                out["data"] = c["data"]
            if c.get("needs_admin_token"):
                out["needs_admin_token"] = True
        out["modes"] = _modes(c)
        out["state"] = "stateless" if tier == "health" else "stateful"
        out["max_duration_sec"] = 30
        return out

    # Fallback — shouldn't happen
    out["type"] = "unknown"
    out["_orig"] = c
    return out


def migrate_test(name: str, spec: dict) -> dict:
    """One entry from test-registry.yaml['tests'] → new schema."""
    out = {
        "type": "script",
        "script": spec["script"],
        "modes": spec.get("runs_in") or [],
        "state": "stateful",
        "max_duration_sec": spec.get("max_duration_sec", 300),
    }
    if "steps" in spec:
        out["steps"] = spec["steps"]
    if "features" in spec:
        out["features"] = spec["features"]
    if "tier" in spec:
        out["tier"] = spec["tier"]  # cheap | meeting | human
    if "requires" in spec:
        out["requires"] = spec["requires"]
    return out


def main() -> int:
    checks_path = T3 / "checks" / "registry.json"
    tests_path = T3 / "test-registry.yaml"
    out_path = T3 / "registry.yaml"

    checks = json.loads(checks_path.read_text()).get("locks", [])
    tests = yaml.safe_load(tests_path.read_text()).get("tests", {})

    merged = {}
    collisions = []

    for c in checks:
        cid = c.get("id")
        if not cid:  # divider entries {"_": "═══ ═══"} etc
            continue
        if cid in merged:
            collisions.append(cid); continue
        merged[cid] = migrate_check(c)

    for tid, spec in tests.items():
        if tid in merged:
            collisions.append(tid); continue
        merged[tid] = migrate_test(tid, spec)

    if collisions:
        print(f"ERROR: id collisions: {collisions}", file=sys.stderr)
        return 1

    # Order: by type, then id
    ordered = dict(sorted(merged.items(), key=lambda kv: (kv[1].get("type", ""), kv[0])))

    header = (
        "# tests3 Registry — consolidated.\n"
        "# Single source of truth for all automated evidence.\n"
        "# Schema: each entry has `type:` (grep | http | env | script).\n"
        "# Generated from tests3/checks/registry.json + tests3/test-registry.yaml\n"
        "# by tests3/lib/migrate-registry.py. See tests3/README.md §3.3.\n\n"
    )
    body = yaml.safe_dump(ordered, default_flow_style=False, sort_keys=False, width=120)
    out_path.write_text(header + body)

    # Stats
    by_type = {}
    for v in merged.values():
        t = v.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1
    print(f"  wrote {out_path.relative_to(ROOT)}")
    print(f"  total entries: {len(merged)}")
    for t, n in sorted(by_type.items()):
        print(f"    {t}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
