#!/usr/bin/env bash
# Runs in the CARVE working dir after each materialize. Deterministic edits only.
set -euo pipefail

# Prune the CALM model to the carve: drop nodes pointing at dropped client dirs
# (clients/dashboard — the commercial UI; clients/extension and deploy/contracts are
# not in CARVE_INCLUDE; clients/slim IS carved) + any relationships
# referencing those nodes, so architecture.calm.json reflects the contributed tree
# (load-bearing for FINOS).
if [ -f architecture.calm.json ]; then
  python3 - <<'PY'
import json
DROP_PATHS = ("clients/dashboard", "clients/extension", "deploy/contracts")
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

# De-robot the Vexum surface (vexa-platform#239): noindex every docs.core.vexa.ai page.
# PROJECTION-ONLY — pairs with the carve/overrides/docs-* files; the mono's docs.json
# must never carry this key (it would noindex docs.vexa.ai).
if [ -f docs/docs/docs.json ]; then
  python3 - <<'PY'
import json
p = "docs/docs/docs.json"
d = json.load(open(p))
d["seo"] = {"metatags": {"robots": "noindex, nofollow"}}
json.dump(d, open(p, "w"), indent=2)
open(p, "a").write("\n")
print("derobot: seo.metatags.robots=noindex,nofollow stamped on docs.json")
PY
fi

# Re-seal the pruned CALM model and regenerate the views derived from it. Both live in
# vexa-core-resident tooling (scripts/ was seeded once and is not carved), and both went
# stale on every train so far (vexa-core cb9f3ff on the 07-29 train, f414502 on the
# 09-02 train) because the calm-prune above changes the chart hash. Deterministic:
# seal = hash of the pruned chart, views = generated from it. No-ops in the mono (which
# is not where transform.sh runs) and on a tree without the scripts.
if [ -f scripts/arch-dsl.mjs ] && [ -f architecture.calm.json ]; then
  node scripts/arch-dsl.mjs --write >/dev/null 2>&1 && echo "arch-dsl: views regenerated" \
    || echo "arch-dsl: regen failed (non-fatal — gate:dataflow will say so)"
fi
if [ -f scripts/gates.mjs ] && [ -f architecture.seal.json ]; then
  node scripts/gates.mjs seal-arch >/dev/null 2>&1 && echo "seal-arch: re-sealed" \
    || echo "seal-arch: re-seal failed (non-fatal — gate:dataflow will say so)"
fi
