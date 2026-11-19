# terminal/src/app/api/join/preview

`GET /api/join/preview?i=<token>` → agent-api's
`GET /api/workspace/invites/preview?token=`, passed through verbatim.

Anonymous by design: the invite card renders before sign-in, so the visitor
learns what they are being asked to join before being asked who they are.
Whoever holds the token may see the workspace's name, the role, who shared it,
and — for a bound invite — the address it admits, which the page prefills and
locks. A token matching nothing gets agent-api's 404 through unchanged, so this
route never enumerates workspaces.

Unset `AGENT_API_URL` answers 503, not 404: the invite may be fine and this
deployment simply cannot ask about it.
