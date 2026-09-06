# app/w

The canonical document route. `/w/<workspace>/<path>` renders the same shell `/` does; the shell
resolves the workspace against the server and opens that file in the pages panel.

The CANONICAL form names the workspace's IMMUTABLE id (`shared/workspace_id.py`), which is the whole
of PRD decision 26: the same link works in a mail, in a chat and inside another workspace's document,
and it still works after the workspace has been renamed, promoted, un-shared or moved. **A SLUG is
accepted too** (Vexa-ai/vexa#1643) — it is what every other surface still spells and what a person
pastes, and a URL that silently is not a URL is worse than either opening the page or saying why not.

Access is the SERVER's answer and never the URL's. A canonical link is routinely handed to people
who cannot open it, and that is by design — but `not-yours` and `gone` open **one sentence in the
panel**, never the desk's README standing in for an answer. The parsing contract lives in
`../workspaceRoute.ts` (pure, unit-tested), the decision in `minutes/deepLink.ts` (pure,
unit-tested), and the three access states are rendered in prose by `ui-kit/WsLink.tsx`.
