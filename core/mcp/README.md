# `core/mcp` — the Vexa control MCP, as an edge

**What it is.** One MCP surface over the whole machine: 64 tools across identity, meetings,
workspaces, flows, friction, rehearse, panel and docs. A person's Claude Code or Codex connects to
it over streamable HTTP with their own token, and drives Vexa from their own chat.

**Where it sits.** Beside `core/gateway`, and for the same reason. It is an **edge**: it exposes the
domains with the caller's identity attached and owns no state of its own. It is not a domain (it has
no store) and not a client (it is a service in the stack, fronted at `/mcp`).

```
a person's agent ──HTTP──▶ gateway /mcp ──▶ mcp-control ──▶ gateway     meetings, transcripts, bots
                                                        ├─▶ agent-api   workspaces, queue, claims, settings, friction, terms
                                                        ├─▶ admin-api   accounts, per-person keys
                                                        └─▶ flows-api   facts, reactions, timeline
```

**It does not run on a laptop.** "Local mode" is about where the WORKSPACE lives, not where this
runs: `workspace_regime(mode="local")` means the person's files are on their own machine and no
cloud agent runs for them — the workspace verbs still operate on the cloud copy, git
(`workspace_pull` / `workspace_push`) is the sync, and their own agent writes the local files with
its native tools.

## The rule this package exists to hold

Every tool is a **thin forward**: build a request, call the HTTP client, shape the response.
`tests/test_thin_forward.py` walks the AST of every tool and fails on

1. any `subprocess`, `docker`, `psycopg` or `sys.path` mutation — the four reaches that made the
   predecessor un-packageable;
2. any tool naming more than one service base URL;
3. any filesystem write outside `VEXA_HOME`;
4. a tool body over the statement budget, or a string literal over 400 characters that is not its
   own docstring — product copy belongs in `_global`, read hot, not in an image.

Every relaxation is one allowlist entry naming the tool and the backlog behind it.

## Where this came from

`deploy/dogfood/rig/vexa_control_mcp.py`: 5,033 lines in one file, 64 tools, a 187-line instruction
literal, a 740-line ASGI middleware, no `pyproject.toml`, no tests, no CI — and it could not start
without a docker socket, two hardcoded container names, a Postgres URL and source checkouts of two
other trees. The full inventory is `~/dev/biz/drafts/2026-09-02-mcp-seam-inventory.md` (B1, B6).

That path is now a 34-line shim that imports this package, because `rig.sh` and a live `~/.storm`
symlink name it and a rig is running from it. The 5,033 lines are gone either way.

## Running it

```bash
uv run --with 'mcp>=2.1,<3' --with uvicorn --with pytest python -m pytest -q   # the suite
python -m vexa_mcp                 # streamable HTTP on $PORT (default 18310), path /mcp
python -m vexa_mcp --stdio         # the offline test transport, not a product path
```

Configuration is env, service-shaped, declared in `config.v1.json` and checked by
`gate:config-contract`. `VEXA_URL` is the one value a deployment must name; the sibling URLs default
to the in-network service names.
