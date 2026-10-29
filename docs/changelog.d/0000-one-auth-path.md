- **One recommended way to send a websocket key.** Send it in the `X-API-Key` header, or from a
  browser in the subprotocol list (`["vexa.v1", "vexa.key.<key>"]`). A key in the URL (`?api_key=`) is
  deprecated: `/ws` still accepts it until 0.13.6 and logs each use (`ws_query_key_deprecated`); the
  bot-account live view refuses it with an `api_key_query_removed` frame (`changed_in`, `do_instead`,
  `docs`) and close `4401`. See [What changed in 0.13.4: auth](/changes-0-13-4-auth).
- **The 0.12 compatibility switches are gone.** `VEXA_WS_SCOPE_MODE`, `VEXA_WEBHOOK_STORED_URL_POLICY`
  and `VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S` are not read. Every webhook delivery is held to the
  outbound-url.v1 guard. Until 0.13.6, as fixed behaviour: `/ws` serves 0.12 clients as 0.12 did, and
  a 0.12 bot already in a call when meeting-api starts keeps storing its transcript for 4 hours. A
  service still given one names it at start (`config_key_removed`); Helm refuses `compat.*`. See
  [What changed in 0.13.4: auth](/changes-0-13-4-auth).
- **The `ws.v1` socket protocol is re-sealed in place.** It adds the `vexa.key.<key>` subprotocol and
  marks the key in the URL and scope-free subscribe as deprecated, removed in 0.13.6. Clients already
  deployed need no change in 0.13.4. See [What changed in 0.13.4: auth](/changes-0-13-4-auth).
- **Compose has no dashboard profile.** The deprecated 0.10 dashboard (`--profile dashboard`,
  `vexaai/dashboard:0.10.6.3.14`) sent its key in the `/ws` URL and is gone from Compose; use the
  Terminal.
