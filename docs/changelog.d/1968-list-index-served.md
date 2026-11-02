- **Meeting lists answer fast again under load (#1968).** `GET /meetings`, `GET /bots`,
  `GET /bots/status` and the shared rows of `GET /recordings` read each access path (your meetings,
  meetings shared with you, your workspaces' meetings) through its own index and then fetch only the
  page's rows. In the 0.13.4 candidates every list call scanned the whole meetings table (about 2.4 s
  each), which exhausted meeting-api's database pool at production load and answered 5xx on
  `/meetings`, `/transcripts`, `/bots` and `/mcp`. Rows, order, paging and `has_more` are unchanged.
  See [Meetings API](/api/meetings).
