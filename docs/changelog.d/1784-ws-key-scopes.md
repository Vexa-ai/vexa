- **The live WebSocket honours key scopes (#1784).** `/ws` now applies the REST routes' scopes: a key
  that cannot read meeting status (`GET /meetings`, `GET /bots/status`) — today only a `browser`-only
  key — is answered `insufficient_scope` and closed with `4403`.
