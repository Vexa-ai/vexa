# app/api/[...path]

Catch-all Next route that proxies terminal API calls to the local gateway surface. It keeps browser code same-origin while the backend ports stay server-side.

Meeting-domain paths (`proxyMode.ts` `MEETINGS_DOMAIN`, including `bot-accounts`) go to the gateway root; the rest to `/agent/*`. Responses are JSON, except SSE, recording media, and a browser session's live view (`bot-accounts/sessions/<id>/view/*`, the noVNC web client), whose bytes and content type pass through.
