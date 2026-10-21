# meeting-api one-off scripts

Operator scripts that run against a meeting-api database, inside the meeting-api image (it carries
`sqlalchemy` + `asyncpg`; the test venv does not). Each defaults to a read-only dry run.

| Script | What it does |
|---|---|
| `backfill_outcome_class.py` | Grades terminal meetings written before `outcome_class` existed, with the same classifier the write path uses (`meeting_api.lifecycle.outcome`). `--dry-run` (default) prints class and reason counts; `--apply` writes `data.outcome_class` / `data.outcome_reason` only where absent. |
