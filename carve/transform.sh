#!/usr/bin/env bash
# Runs in the CARVE working dir after each materialize. Deterministic edits only.
set -euo pipefail

# Prune the CALM model to the carve: drop nodes pointing at dropped client dirs
# (clients/dashboard — the commercial UI; clients/slim IS carved) + any relationships
# referencing those nodes, so architecture.calm.json reflects the contributed tree
# (load-bearing for FINOS).
if [ -f architecture.calm.json ]; then
  python3 - <<'PY'
import json
DROP_PATHS = ("clients/dashboard",)
d = json.load(open("architecture.calm.json"))
nodes = d.get("nodes", [])
dropped = set()
for n in nodes:
    if any(p in json.dumps(n) for p in DROP_PATHS):
        dropped.add(n.get("unique-id"))
dropped.discard(None)
d["nodes"] = [n for n in nodes if n.get("unique-id") not in dropped]
def keep_rel(r):
    s = json.dumps(r)
    if any(p in s for p in DROP_PATHS):
        return False
    return not any(nid and nid in s for nid in dropped)
d["relationships"] = [r for r in d.get("relationships", []) if keep_rel(r)]
json.dump(d, open("architecture.calm.json", "w"), indent=2)
open("architecture.calm.json", "a").write("\n")
print(f"calm-prune: removed nodes {sorted(dropped)}")
PY
fi

# Drop the internal "observe" script (pointed into the purged core/meetings/eval).
if [ -f package.json ]; then
  if command -v jq >/dev/null; then
    tmp=$(mktemp); jq 'del(.scripts.observe)' package.json > "$tmp" && mv "$tmp" package.json
  else
    python3 - <<'PY'
import json
d=json.load(open("package.json")); d.get("scripts",{}).pop("observe",None)
json.dump(d,open("package.json","w"),indent=2); open("package.json","a").write("\n")
PY
  fi
fi
