- **Agent turns can reach the open web: `WebSearch` + `WebFetch`.** The `openai-agent` harness — the
  loop Vexa runs against a model you host yourself — had the workspace file tools and nothing else,
  so a turn told to *research first, ask last* had no way to research anything. `WebFetch` reads one
  `http(s)` page as text and is always available. `WebSearch` speaks a small adapter to a search
  endpoint **you supply** (`VEXA_SEARCH_URL` + `VEXA_SEARCH_DIALECT`: `searxng` or `brave`); with
  none configured it is simply absent from the turn's tool list. **No search engine ships with
  Vexa** — no image, no compose service, no vendored code. `WebFetch` refuses any address on the
  deployment's own network, re-checking every redirect hop. See
  [Agent web search](/agent-web-search).
