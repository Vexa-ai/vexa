# mcp.tools.v1 — the MCP tools a domain serves, and the door behind each

The MCP (`core/meetings/services/mcp`) is an edge, like the gateway: it asks each deployed domain what
it serves and presents the union as one tool surface (`vexa_mcp/manifest.py`). A domain declares its
tools in an `mcp.tools.v1.json` beside its service (`core/agent/`, `core/flows/`). A private deployment
mounts more as `*.mcp.tools.v1.json`; the loader stamps those `mounted`, and they compete for names with
no precedence.

Each tool states who the caller must be (`identity`), which credential the edge presents to the door
(`auth`: the caller's own, a key the deployment holds, or none — required, never defaulted), which
domains it needs (`requires`), and its route. Manifest-level policy: `depends_on` (identity only),
`forward` (call tools back through the gateway, the mapping the domain's `routes.v1` gives it),
`admin_auth` (the header and setting of the deployment's key, required when any tool is `auth: admin`),
`composes` (a harness that may require any known domain), and `entitlement` (the one hook that answers
whether a person may act).

A manifest with a key this schema does not name is refused, so a new policy key is a contract change
(lane:contract). The loader refuses the same things at boot.

`node validate.mjs --check` checks the goldens and every manifest under `core/`, then that no tool name
is claimed twice and that there is at most one entitlement hook. Goldens: `Manifest.*` conform;
`Refused.*` carry a manifest that must be refused and the reason.
