# bot-limits.v1 goldens

Filename = `<Shape>.<case>.json`; the part before the first dot is the `$def` it conforms to
(`BotLimits`, `Resolution`). Validated by `../validate.mjs` (gate:schema). The `Resolution` vectors are
replayed by admin-api (`tests/test_bot_limits_context.py`: each `expect` is what bot-context answers),
and every `BotLimits` golden is read by meeting-api (`tests/test_limits.py`), so both sides are held
to the same files.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
