### Fixed

- **`GET /bots/status` no longer reads a caller's entire meeting history to answer a running-bots
  badge.** It fetched every meeting the account ever had, with the full `data` JSONB, and filtered
  in Python — 4,896 meetings / 180 MB on one production account, 144 MB of that `bot_logs` no
  endpoint renders. Four concurrent polls demanded roughly 740 MB transiently and OOM-killed the
  pod at the default 1 GiB limit. The status filter and the heavy-key projection now both happen in
  the query. ([#803](https://github.com/Vexa-ai/vexa/issues/803))
- **`GET /meetings/{id}` fetches the row instead of enumerating the account and filtering by id.**
  Access rules are unchanged — the same ownership/share union decides visibility — and the detail
  view still returns full `data`. ([#803](https://github.com/Vexa-ai/vexa/issues/803))
