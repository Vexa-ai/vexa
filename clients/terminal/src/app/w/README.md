# app/w

The canonical document route. `/w/<workspace-id>/<path>` renders the same shell `/` does, then
resolves the workspace id against the server and opens that file in the panel.

The id is the workspace's IMMUTABLE id (`shared/workspace_id.py`), never its slug — which is the
whole of PRD decision 26: the same link works in a mail, in a chat and inside another workspace's
document, and it still works after the workspace has been renamed, promoted, un-shared or moved.

Access is the SERVER's answer and never the URL's. A canonical link is routinely handed to people
who cannot open it, and that is by design — a `not-yours` answer opens nothing and leaves the
terminal as it was. The parsing contract lives in `../workspaceRoute.ts` (pure, unit-tested); the
three access states are rendered by `ui-kit/WsLink.tsx`.
