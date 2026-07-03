# deploy/compose/bin — the gate:compose runner

`stack-test` is the one entrypoint the `gate:compose` gate dispatches: it detects docker (skip green
if absent), then runs the stack-readiness proof (`../tests/stack_test.py`) via `uv run pytest`. The
proof owns the full up→prove→`down -v` lifecycle itself.

```bash
bin/stack-test                  # always-on subset (the routine gate)
COMPOSE_BOT=1 bin/stack-test    # + the real bot-spawn proof (steps 3·6a)
COMPOSE_NO_BUILD=1 bin/stack-test   # reuse cached images (skip --build)
```
