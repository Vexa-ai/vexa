- **The ZAKI agent can read meetings again: the Minutes read index is ordered by update time (#48).**
  `GET /api/zaki/read/v1/{userId}/index` now orders its page by `updated_at`, the same clock it
  already filters (`since`), snapshots and pages on — matching `/search` and the shared item
  projection. Ordering the page by occurrence instead put a meeting's freshly-written summary behind
  its older transcript under the shared `occurred_at`, so `updated_at` rose across the page and the
  read client refused the whole response; any user with a summarised meeting lost every index read.
