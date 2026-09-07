# deploy/dogfood — the long-lived MCP validation + dogfooding stack

A Vexa stack that stays up, fronted by a real hostname over real TLS, so two things can happen that
a `docker compose up` in a terminal cannot do:

1. **Validate the MCP service as deployed.** `core/meetings/services/mcp/tests/` proves the app
   in-process against a faked gateway. That is the right test and it does not answer the question
   an MCP client asks: *does the transport work through the proxy chain, with a real key, against a
   real gateway?* `bin/mcp-validate` answers that one.
2. **Dogfood it.** Point Claude at it and use Vexa the way a customer would — which is the only way
   the onboarding defects surface, because they are all in the part no test covers: getting a key,
   storing it, and attaching the server.

## There is no compose overlay here, deliberately

This directory holds a `.env`, an nginx vhost, a probe, and a Makefile. It drives the **stock**
`deploy/compose/docker-compose.yml` unchanged.

That is the design, not laziness. A dogfooding stack that differs structurally from what users run
stops being evidence about what users run. Everything this directory sets is a *value* — ports,
image tag, secrets, hostnames — never a structural change. If dogfooding ever needs a change to the
stack's shape, that change belongs in the stack, where users get it too.

## Layout

| File | What |
|---|---|
| `env.dogfood.example` | the `.env` for the stock stack — a port block offset from the compose defaults so this can sit beside a release-train stack with no collision |
| `nginx/mcp.dev.vexa.ai.conf` | the two vhosts, on the host's existing `*.dev.vexa.ai` wildcard cert |
| `bin/mcp-validate` | drives the endpoint as a real MCP client; stdlib-only, runs from anywhere |
| `Makefile` | `up · down · ps · logs · key · validate · connect · nginx-check` |

## Hostnames

Both are **one label** under `dev.vexa.ai`, because the host's wildcard DNS record and wildcard
certificate each cover exactly one level — a four-label name would need its own cert and record.

| Host | → | What it is |
|---|---|---|
| `mcp.dev.vexa.ai` | gateway `127.0.0.1:18456` | the MCP endpoint (`/mcp`) and the API front door |
| `dogfood.dev.vexa.ai` | terminal `127.0.0.1:15400` | where a human signs in and copies an API key |

## Bring-up

On the stack host (never a laptop — container workloads run on `bbb`):

```bash
cd deploy/dogfood
cp env.dogfood.example .env.dogfood     # then fill every CHANGE-ME
make up                                 # pulls published images, starts the stock stack
make key EMAIL=you@example.com          # mint an API key — prints only the token
sudo cp nginx/mcp.dev.vexa.ai.conf /etc/nginx/sites-available/mcp.dev.vexa.ai
sudo ln -s /etc/nginx/sites-available/mcp.dev.vexa.ai /etc/nginx/sites-enabled/mcp.dev.vexa.ai.conf
sudo nginx -t && sudo systemctl reload nginx
```

`make up` refuses to run while `CHANGE-ME` remains in the env file. This stack is internet-reachable
through nginx, and the compose dev defaults (`ADMIN_TOKEN=dev-admin-token`, `DB_PASSWORD=postgres`)
are not acceptable on something with a public hostname. `make up` also never builds: it pulls the
published, release-validated tag, because a dogfood stack running a local source build proves
nothing about the release.

## Handing the instance back to first run

A first-run rehearsal needs an instance that has never been claimed, and a long-lived dogfood stack
is the opposite of that: the admin is whoever signed in first — usually a test identity — and the
setup wizard is marked complete. Neither fact can be undone from any product surface.

```bash
deploy/dogfood/bin/reset-instance [--admin-email minutes-test@vexa.ai] [--keep-global]
```

Three values, named, and nothing else: the admin role is released so the next sign-in claims it, the
first-run wizard state is cleared, and the company-layer gate goes back up. **It deletes nothing** —
workspaces, chats, meetings, transcripts and `_global/asks/` all survive. That narrow blast radius is
the point: the account it has to reset sits next to the founder's, and the alternative that existed
before this script was hand surgery on a jsonb column by whoever remembered the query.

After it runs, until the admin's setup chat calls `mark_global_ready`:

- a non-admin sign-in is refused with one sentence, and no user row is created for them;
- the flows engine **parks** every fact instead of sending — nothing claimed, nothing failed, no
  attempt burned, arrival order kept;
- `flows_submit` and `flow_lifecycle` refuse by name.

`--keep-global` leaves the company layer accepted, for rehearsing the claim without re-typing the
company.

## Validating

```bash
VEXA_API_KEY=vxa_… make validate          # the public endpoint, through nginx
VEXA_API_KEY=vxa_… make validate-local    # host-local, bypassing nginx — isolates proxy faults
```

The probe reports two families, and the split is the point:

**CONFORMANCE — is this an MCP server?** `initialize` · `tools/list` against the exact 9 tools the
service declares · `prompts/list` against the 4 prompts · a pure `parse_meeting_link` call (no
gateway hop, so it isolates the transport) · a real `list_meetings` call (the full
MCP → gateway → meeting-api path with your key) · and a fail-closed check that a bogus key is
*rejected* rather than passed through as an anonymous call. Drift in the tool set fails either
direction: a missing tool is a regression, an undeclared extra one is a README that stopped being
true. These govern the exit code.

**NATIVE-READY — is this a connector, or does a human still paste a key?** RFC 9728
protected-resource metadata · the `WWW-Authenticate` challenge on 401 · authorization-server
metadata. These are the mechanical difference between "add a custom MCP server and paste a header"
and what Gmail does — click connect, log in in a browser, never see a credential. They are
**expected to fail today**; the probe prints them as a measured distance rather than hiding them,
and `--require-native` makes them binding once that work is expected to hold.

## What is verified as of 2026-08-29

Against `https://mcp.dev.vexa.ai/mcp`, images `v0.12.23`, through nginx and TLS:

- All 6 conformance checks pass. Server identifies as `Vexa MCP Service (v0.12) v1.28.1`,
  protocol `2025-06-18`, sessioned.
- The `GET /mcp` SSE leg survives the proxy: headers arrive immediately, `content-type:
  text/event-stream`, the stream then holds open silently with no gateway-manufactured 503. This
  is the `Vexa-ai/vexa#795` failure mode not happening, and it is why the vhost keeps
  `proxy_buffering off` (inherited from `proxy_vexa.conf`) and additionally disables `gzip` — a
  compression filter re-buffers an event stream even when proxy buffering is off.
- All 3 native-ready checks report GAP. The endpoint is key-paste only.

## Attaching Claude

```bash
claude mcp add --transport http vexa-dogfood https://mcp.dev.vexa.ai/mcp \
  --header "Authorization: Bearer $VEXA_API_KEY"
```

Two known sharp edges in that line, both of which the native-ready work removes rather than
documents around:

- `claude mcp add` has been observed writing **resolved** values back into `.mcp.json` instead of
  preserving `${VAR}` syntax, which turns a shared project config into a committed credential.
  Keep this server in user scope, not a checked-in `.mcp.json`.
- Claude Code strips environment variables whose names contain `KEY`, `TOKEN`, `SECRET` and
  similar from spawned server processes. That sanitizer is about child-process env, not about
  header expansion, but it is close enough to the blast radius that a name like `VEXA_API_KEY`
  deserves an actual check on your machine rather than an assumption.
