# signals tests

`uv run pytest -q` from `core/signals`. No network and no containers: redis is `fakeredis`, the model,
agent-api, the broker and flows-api are `httpx.MockTransport`s. Every transcript line is invented.

| file | covers |
|---|---|
| `test_scoring.py` | the batch-score formula and every wake reason |
| `test_brief.py` | brief defaults, bounds, `addressed_to_vexa` restored |
| `test_policy.py` | off unless on + a model; caps and their ceilings; model_signals parsing |
| `test_model.py` | both adapters' request shape and strict parsing; garbage refused |
| `test_keys.py` | broker read, `none`, the dev-only fallback |
| `test_core.py` | buffer, cap refusals before any model call, wake data |
| `test_runner.py` | end to end over fakeredis: discovery, annotations, wakes, end, lease takeover with no double scoring |
| `test_contract.py` | signals.v1 goldens, and what the code writes conforms |
| `test_health.py` | `/health` |
