"""workspaces.shared — the portable workspace primitives (PRD decision 47, step 2).

Moved out of core/agent/shared: workspace_id (identity/minting), workspace_paths (path
containment), entities (EntityFrontmatter read/write, the entity_upsert seam), links (the
[[ws:<id>/<slug>]] grammar + cross-workspace resolution). Zero-HTTP, stdlib + git only — no
FastAPI/pydantic here, unlike core/agent/shared's own __init__ (see core/flows/eval/dna/score.py's
comment on why that matters: importing a submodule still runs the package __init__). That is the
point of this package existing on its own: a workspace can be read and written with no service
around it at all (the "local mode" this move proves).

Deliberately NO convenience re-exports here (unlike core/agent/shared/__init__.py) — import the
explicit submodule (e.g. ``from workspaces.shared.entities import upsert_entity``).
"""
