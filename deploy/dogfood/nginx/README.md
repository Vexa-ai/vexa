# deploy/dogfood/nginx — the vhosts

`mcp.dev.vexa.ai.conf` serves the dogfood stack on two hostnames, both **one label** under
`dev.vexa.ai` because the host's wildcard DNS record and wildcard certificate each cover exactly
one level — a four-label name would need its own cert and its own record.

| host | → | what it is |
|---|---|---|
| `mcp.dev.vexa.ai` | gateway `127.0.0.1:18456` | the MCP endpoint (`/mcp`) and the API front door |
| `dogfood.dev.vexa.ai` | terminal `127.0.0.1:15400` | where a human signs in and copies an API key |

An explicit `server_name` takes precedence over the `*.dev.vexa.ai` wildcard block, which
otherwise routes everything to the K8s ingress.

## Why the MCP vhost is not a stock proxy block

The transport's two legs are not the same animal, and conflating them is a documented failure
(`Vexa-ai/vexa#795`): `POST /mcp` is a short request/response, but `GET /mcp` is the server→client
SSE stream — headers, then silence until the server pushes. A proxy that buffers that leg waits on
the next body read of a healthy stream, hits its read timeout, and answers with a
gateway-manufactured 503 the MCP service never saw.

The shared `proxy_vexa.conf` already carries what that needs (`proxy_buffering off`,
`proxy_read_timeout 86400`). This vhost adds the two things it does not: `gzip off` — a compression
filter re-buffers an event stream even when proxy buffering is off — and
`proxy_request_buffering off`.

Install:

```bash
sudo cp mcp.dev.vexa.ai.conf /etc/nginx/sites-available/mcp.dev.vexa.ai
sudo ln -s /etc/nginx/sites-available/mcp.dev.vexa.ai /etc/nginx/sites-enabled/mcp.dev.vexa.ai.conf
sudo nginx -t && sudo systemctl reload nginx
```
