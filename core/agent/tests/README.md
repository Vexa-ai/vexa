# tests — agent-api (L1 + L2 of the validation pyramid)

`uv run pytest -q` (driven by `gate:python`).

| File | Level | Proves |
|---|---|---|
| `test_contracts_consumer.py` | L1 contract | transcript.v1 + workspace.v1 goldens validate by-path; meetings internals are un-importable |
| `test_core_run.py` | L2 unit | `core.run()` with **faked ports**: reads a transcript.v1 golden → emits the stub action → would-commit a workspace.v1-conformant entity |
| `test_config.py` | unit | `VEXA_*` env validates + fails fast; secrets stay out of repr; worker `env` matches `runtime.v1` golden |
| `fakes.py` | — | in-memory `WorkspacePort` / `RuntimePort` — the seam that makes L2 possible |
| `conftest.py` | — | loads transcript.v1 goldens by path (the spec, P8) |
