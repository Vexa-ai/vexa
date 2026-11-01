- **Expired API keys leave the key list, and a one-off removes them (#1868).** Before 0.13.4, Flows minted
  a 15-minute key on the account for each gateway read, so key lists filled with hundreds of expired keys.
  0.13.4 stops the minting; `GET /admin/users/{id}/tokens` (the account page's key list) now skips any token
  whose expiry has passed. Operators can delete the expired rows already stored with
  `python -m admin_api.prune_expired_keys` (dry run by default, with the cutoff it used and a breakdown by
  key name and scopes; `--apply --max N --before <that cutoff>` deletes at most the agreed count, only
  `kind = key` rows expired before the cutoff, by default over an hour ago).
