# searxng — the operator's search endpoint

**One sentence:** the `openai-agent` harness's `WebSearch` needs a search endpoint, and this profile
starts one for a deployment that does not already have one.

## SearXNG is not part of Vexa

[SearXNG](https://github.com/searxng/searxng) is **AGPL-3.0**. The compose block pins an
**unmodified** upstream image by digest, which **you** pull from SearXNG's own registry and run as a
separate service beside the stack. It is never built into or redistributed inside a `vexaai/*`
image, so no AGPL source-offer obligation attaches to Vexa's Apache-2.0 artifacts — it rests with
the upstream image you run. Declared `disposition: sidecar` in `image-licenses.json`, the same
disposition as `minio/minio`. Nothing starts unless you name the profile.

There is **no Helm component** for this, deliberately: on Kubernetes you run your own and point
`VEXA_SEARCH_URL` at it.

## Run it

```bash
docker compose --profile search up -d searxng
```

Then name it to the harness in `deploy/compose/.env` — the runtime forwards these into every spawned
worker:

```
VEXA_SEARCH_URL=http://searxng:8080
VEXA_SEARCH_DIALECT=searxng
```

The adapter takes **any** endpoint of that shape; this one is a convenience, not a dependency. The
[Brave Search API](https://brave.com/search/api/) (`VEXA_SEARCH_DIALECT=brave`) needs no server at
all. See `docs/docs/agent-web-search.mdx`.

## The three load-bearing settings

Two live in `settings.yml` and one in compose. All three were found by running it, and each fails in
a way that does not look like its cause:

| Setting | Where | Without it |
|---|---|---|
| `search.formats` includes `json` | `settings.yml` | SearXNG's JSON output is off by default — every `WebSearch` gets `403` |
| `server.limiter: false` | `settings.yml` | the bot detection refuses the JSON format for a non-browser client, which is exactly what the harness is |
| `GRANIAN_HOST=0.0.0.0` | compose `environment:` | the image defaults granian to `::`; a docker network without IPv6 has no `::` to bind, so the server dies at socket init while the container stays up and the port answers nothing |

The limiter is safe to disable **here** because the service publishes to loopback only and is
otherwise reachable just from the private compose network. Do not expose this port publicly.

## Configure

| Variable | Meaning |
|---|---|
| `SEARXNG_SECRET` | `server.secret_key`. SearXNG reads it from the **environment** — it does not expand `${...}` inside the mounted `settings.yml`, so the secret never lives in this directory. Set a real one for anything that is not a local stack. |
| `SEARXNG_HOST_PORT` | loopback publish port (default `18481`); compose DNS is unaffected |
| `SEARXNG_IMAGE` | overrides the digest-pinned default — bump it deliberately, never implicitly |

`settings.yml` is mounted **read-only**: the settings are ours to state, not the container's to
rewrite. The image's entrypoint only generates its own when none is mounted, so this file wins.
Everything else comes from `use_default_settings: true`, so an upstream bump brings upstream's
defaults with it.
