# version

`GET /api/version` — what is serving, so a tab can tell when the deployment underneath it moved.

PRD decision 39 removed the "out / in" ritual: containers are replaced beside the running ones and
traffic is switched with nobody asked to leave. The ritual's one real product was a person who was
never looking at a page older than the service behind it, and this route is how that is bought
back.

It answers both halves in one reading:

```json
{ "terminal": { "build": "line-6bec34db4", "agent_api": 1 },
  "server":   { "sha": "line-6bec34db4", "api": 1 },
  "paired":   true }
```

- **`terminal.build`** — `NEXT_PUBLIC_BUILD_ID`, inlined into the client bundle at image build. A
  tab running an older bundle carries the older string; the server route serving the new one
  reports the new string, and that difference IS "a new version is ready".
- **`server`** — agent-api's own `GET /api/version`, fetched server-side through `AGENT_API_URL`,
  i.e. through the compose network alias the blue/green swap moves. The answer therefore changes
  the instant traffic is switched, with nothing restarted. Unreachable → `null`, never a 500:
  agent-api is briefly unreachable during every swap by construction, and a probe that failed
  loudly would paint an error in the client each time a container was a second slow.
- **`paired`** — `false` when the server answers a contract number this bundle was not built for
  (F55/F77). `deploy.sh` refuses to create that state; if one is live anyway, the client offers a
  reload, which is the only move it has.

It is a route of its own rather than a hop through `[...path]`, which carries a per-user API key to
the gateway — the tab most likely to be running a stale bundle is one with no session at all. It is
also the only place that knows the bundle's own build id.

Consumers: `src/app/versionWatch.ts` (the decision logic) and `src/app/VersionBar.tsx` (one line, a
button, and a reload that happens only on the click). The constants both sides compare live in
`src/version.ts`, and `Dockerfile`'s `ai.vexa.terminal.agent_api` label repeats the pairing number
so a swap can read it without starting a container — a test pins the two together.
