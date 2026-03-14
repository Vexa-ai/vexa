# Agent Test: Staleness and Dead Code Detection

## Prerequisites
- Run `python tests/audit/staleness_audit.py` first
- Read its output from tests/audit/results/

## Tests

### Test 1: Verify script findings
Goal: Review the staleness_audit.py output and validate each finding
- Some findings may be false positives (e.g., dynamically imported modules)
- Classify each as: confirmed stale | false positive | needs investigation
- Pay special attention to "unimported" findings — check for dynamic imports, `importlib`, or plugin patterns
- For "unreferenced definitions": check if they are part of a public API or called via framework magic (e.g., FastAPI routes, Celery tasks)

### Test 2: Semantic staleness
Goal: Find things the script can't detect
- Read each service's README and compare to actual code structure
- Check if documented features still exist and work
- Check if there are experimental/WIP directories that should be cleaned up
- Check the `experiments/` directory — is anything there production-ready or dead?
- Check `testing/` directory at repo root — are those scripts current or stale?
- Check `scripts/` directory — are all scripts still used and functional?
- Check `nbs/` directory — are notebooks current or outdated?
- Look for TODO/FIXME/HACK/XXX comments that reference completed or abandoned work

### Test 3: Dependency graph freshness
Goal: Verify the dependency graph is accurate
- Check docker-compose.yml `depends_on` matches actual service dependencies
- Check shared-models / libs are the right version in all service Dockerfiles
- Check if any service has pinned to old versions of internal deps
- Verify that all services listed in docker-compose.yml have corresponding directories
- Check if any service directory exists but is NOT in docker-compose.yml

### Test 4: Git history staleness
Goal: Find abandoned work
- Files changed >6 months ago and not since, in active directories
- Branches referenced in code/docs that no longer exist
- TODO/FIXME comments older than 3 months (use `git blame` to check dates)
- Check for merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) left in files
- Look for `.bak`, `.old`, `.orig`, `.save` files that should be cleaned up

## Output format
For each test, produce a list of findings with:
- **File path** (absolute)
- **Classification**: confirmed stale | false positive | needs investigation
- **Recommended action**: delete | update | merge | keep (with justification)
- **Priority**: high (blocking) | medium (should fix) | low (nice to have)
