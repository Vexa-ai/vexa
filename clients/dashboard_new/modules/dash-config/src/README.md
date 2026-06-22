# dash-config — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Resolves the browser's runtime config (the /api/config logic) as ONE pure function: given the request host/proto + the configured/internal API URLs, return { apiUrl, wsUrl, authToken }. Carries Learning #37's both-loopback fix — gateway-direct WS only when the configured port is the published gateway host port, else same-origin. authToken from a single source: cookie || selfHostKey || null.

