# routes.v1 — the routes a domain asks the gateway to serve, and their policy

Each domain declares its routes in a `routes.v1.json` beside its service (`core/meetings/routes.v1.json`,
`core/agent/routes.v1.json`, …). The gateway assembles its route table from the manifests of the domains
a deployment runs (`core/gateway/services/gateway/src/gateway/routes_manifest.py`): it owns no route list
of its own, and a domain that is not deployed contributes no rows, so its routes answer 404.

A row carries its policy as well as its scopes, so the policy keys are this contract:

| Key | Meaning |
|---|---|
| `scopes` | the key scopes that may call the row (`bot`, `tx`, `browser`). Required; `[]` declares the row unscoped. |
| `delegation` | the row admits a worker's own delegation token. A delegation row is scoped. |
| `mcp_reentry` | the row admits that token only on the MCP's re-entry: one row per route an `mcp.tools.v1` tool calls back. Never beside `delegation`, never on a `{path:path}` catch-all; unscoped only on the edge's own `/auth/me`. |
| `stream` | the edge relays the response as server-sent events. Only on a forwarded domain's literal rows. |
| `upstream` | the route template the edge forwards the row to on the domain's service, when it is not the row's own path (meetings' `/user/webhook/deliveries` is meeting-api's `/webhooks/deliveries`). Its `{name}` parameters are the row's own. The domain's service reads it too: meeting-api checks, on each of its routes, the scopes of the rows that reach it (`meeting_api/route_scopes.py`). Never on a forwarded domain, whose `forward` is the one mapping. |
| `verbs` (manifest) | policy for one verb behind a forward, which the catch-all cannot carry: `{method, path, person}` by public path. `person: true` means the verb needs a person in the loop. The edge does not register these rows; the domain's service reads them (agent-api's `control_plane/route_policy.py`). |
| `forward` (manifest) | the domain is forwarded wholesale: `{edge_prefix}{tail}` goes upstream as `{upstream_prefix}{tail}`. Its rows are then paths under the edge prefix (literal segments and whole `{name}` parameters) or the prefix's `{path:path}` catch-all. |

A manifest with a key this schema does not name is refused, so a new policy key is a contract change
(lane:contract), not a loader edit. `routes_manifest.py` refuses the same things at boot.

`node validate.mjs --check` checks the goldens and then every `routes.v1.json` under `core/`, and that
no two manifests declare the same (method, path). Goldens: `Manifest.*` conform; `Refused.*` carry a
manifest that must be refused and the reason.
