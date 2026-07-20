- **Bot: no more "Socket already opened" on first use (#820).** The redis adapters flipped a
  `connected` flag only *after* `connect()` resolved, so two concurrent first-use callers both saw
  it false and both connected — node-redis v4 throws on the second. Both adapters now share one
  idempotent `makeLazyConnect` memo, so concurrent first-use callers await a single `connect()`.
