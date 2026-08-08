- **The MCP server has a page, and it states the boundary (#1077).** New [MCP server](/vexa-mcp)
  page lists the nine tools, gives a working client config, and says plainly what the surface does
  and does not cover: fronted by the gateway at `/mcp` on self-hosted compose from 0.12.18, **not**
  deployed by the Helm chart and therefore unavailable on hosted or Kubernetes, and never proven
  against a real MCP client end to end ([#888](https://github.com/Vexa-ai/vexa/issues/888)). The
  service README's Status block, which still listed the gateway-fronted `/mcp` forward as planned
  while its own body documented the shipped behaviour, now matches.
