# `core/mcp/tests` — what this package is not allowed to become

Four files, and three of them are rules rather than examples.

| file | what it holds the line on |
|---|---|
| `test_thin_forward.py` | **THE rule.** Every tool is a thin forward: build a request, call the HTTP client, shape the response. It AST-walks each tool and fails on any `subprocess` / `docker` / `psycopg` / `sys.path` reach, on a tool naming more than one service base URL, on a write outside `VEXA_HOME`, on a body over the statement budget, and on a string literal over 400 characters that is not the docstring. Every relaxation is one allowlist entry naming the tool and the backlog behind it. |
| `test_parity.py` | The 64 tools of `deploy/dogfood/rig/vexa_control_mcp.py` at `43d824f20` — names, JSON schemas and docstrings — captured through the MCP SDK into `rig_surface.json` and diffed against this package. That surface is what every agent connected to the running rig sees; a port that quietly reworded one would change what they are told without changing anything they could see. |
| `test_delegation_mirror.py` | One delegation verifier. `delegation.py` is `core/agent/shared/delegation.py` VERBATIM, asserted byte for byte — the rig hand-rolled a second HMAC verifier beside the library's, in another image, with no test comparing them. |
| `test_config.py` | Config is a declared contract, from the side `gate:config-contract` cannot see: a key declared and never read fails here, and so does a docker socket, a container name or a database URL reappearing as a deployment input. |

Run them the way `gate:python` does:

```bash
cd core/mcp && uv run pytest -q
```

`rig_surface.json` is a FROZEN fixture, not a generated file. Regenerating it is how a port stops
being a port; if a tool genuinely has to change shape, the change and the fixture edit belong in the
same commit, with the reason in the message.
