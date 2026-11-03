- **The pages an agent read arrive as data (#1815).** When a turn fetches a web page with `WebFetch`,
  the chat stream now carries a `sources` frame with the page's `url` (and `title` when the tool
  reports one), so a client can list the answer's citations without parsing the reply text. Only
  successful `http(s)` fetches count; searches do not. See [Agent API](/api/agent).
