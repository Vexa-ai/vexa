- **One way to send a websocket key.** `/ws` and the bot-account live view refuse a key in the URL
  (`?api_key=`) with an `api_key_query_removed` frame and close `4401`; send it in the `X-API-Key`
  header, or from a browser in the subprotocol list (`["vexa.v1", "vexa.key.<key>"]`). Every such
  refusal carries `changed_in`, `do_instead` and `docs`. See [What changed in 0.13.4: auth](/changes-0-13-4-auth).
- **The 0.12 compatibility switches are gone.** `VEXA_WS_SCOPE_MODE`, `VEXA_WEBHOOK_STORED_URL_POLICY`
  and `VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S` are not read: `/ws` always takes the REST key scopes,
  every webhook delivery is held to the outbound-url.v1 guard, and a transcript entry is stored only
  when it is signed. A service still given one names it at start (`config_key_removed`); Helm refuses
  `compat.*`. Upgrade from 0.12 with no meeting in flight. See [What changed in 0.13.4: auth](/changes-0-13-4-auth).
