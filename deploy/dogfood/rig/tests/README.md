# `deploy/dogfood/rig/tests` — the rig's behavioural tests

Until 2026-09-03 this directory did not exist. `vexa_control_mcp.py` is 5,033 lines and 53 verbs —
the whole agent-facing surface — and **nothing called one of them**: the only file that touched it,
[`core/agent/tests/test_rig_stateless.py`](../../../../core/agent/tests/test_rig_stateless.py),
greps the source text. That is why the shell injection, the missing membership check, the
world-readable credential stores and an operator gate that broke `bot_schedule` were all invisible
to a green suite (review row R-D20).

## Run them

```bash
cd deploy/dogfood/rig && python3 -m pytest tests -q      # stdlib only, no venv needed
```

The suite deliberately needs nothing installed. The server's own runtime dependencies are declared
in `../pyproject.toml` and `rig.sh` builds `../.venv` from it — but a test run that required that
venv would be a test run nobody does from a laptop.

Offline and stdlib-only. `conftest.py` does two things so no test has to:

* **Stubs the MCP SDK** when `mcp.server.mcpserver` is not importable, registering tools in the
  same place the module reads them back (`mcp._tool_manager._tools`) — so a run does not depend on
  an SDK generation being installed.
* **Points every credential store at a tmp directory** (`VEXA_RIG_STATE_DIR`) and supplies the
  keys the module now demands at import. **Nothing here reads or writes a real `~/.storm` file.**

`as_user()` signs a test in and swaps `_http` for a recorder, because what a tool ASKED FOR — the
subject on a query string, the header a secret travels in — is where authorization actually lives.

## What is pinned here

Fourteen gates, one per row of the `rig-authz` / `rig-write-guard` dispatch. Each test names its
row and states the defect it replaces, so a future reader can tell what the assertion is worth.

| gate | row | holds |
|---|---|---|
| 1a·1b | R-D04 | a workspace link carries a short-lived, path-scoped view token, never a durable bearer |
| 2a·2b | R-D05 | credential stores are sealed, `0600` in a `0700` dir; legacy plaintext migrates then goes |
| 3 | R-D06 | the autonomous delegation regime may not `bot_say` or `meeting_delete` |
| 4a·4b | R-D07 | `reaction_signal` steers only the caller's OWN reaction (ownership, not operator authority — cancelling the join you scheduled is the product); `reactions_list` is scoped to the caller |
| 5 | R-D09 | concurrent sign-ins do not lose a token |
| 6 | R-D10 | the transcript converters cannot read or write outside their directories |
| 7a·7b | R-D11 | no account before the code is proven; identical answer either way; single-use codes |
| 8 | R-D12 | `whats_waiting` asks only for this subject |
| 9 | R-D13 | the `/do` bridge never echoes a credential |
| 10 | R-D14 | the credential detector is the one the API uses, imported not copied |
| 11 | R-D19 | `whats_waiting` is `@_anon_guard`-wrapped |
| 12 | R-D21 | the instance-wide friction routes are operator-only |
| 14 | — | **AST**: no tool writes a plaintext credential (the standing check) |

Gate 4c lives with the service that owns the scoping
([`core/flows/tests/test_reactions_scope.py`](../../../../core/flows/tests/test_reactions_scope.py))
and gate 13 with the route it guards
([`clients/terminal/src/app/api/__tests__/minutesSeed.test.ts`](../../../../clients/terminal/src/app/api/__tests__/minutesSeed.test.ts)).

## The runtime gates (added 2026-09-03, from three live findings on deploy)

| gate | finding | holds |
|---|---|---|
| `test_migration.py` | the seal migration was LAZY | every known store is sealed at process start; the registry is total, so a new store cannot dodge it; plaintext this module does not own is reported rather than ignored |
| `test_runtime_deps.py` | the rig had no venv and no dependency declaration | importing `rig_secrets` drags in no control-plane tree; the two borrowed `core/agent` files stay stdlib-pure; every third-party import is declared |
| `test_rig_sh.py` | `rig.sh down` killed a bystander | `stop_by_pidfile` stops the rig and leaves a process whose command line merely *contains* the path alone; a recycled pid is not signalled |

`test_rig_sh.py` drives `rig.sh`'s functions in bash (`RIG_SH_LIB=1 source rig.sh` defines them and
returns before the dispatch). The `pkill` defect was behavioural — the string was in plain sight and
looked fine — so reading the file would not have caught it.

## One authentication path (added 2026-09-03, founder ruling)

`test_single_auth_path.py` holds the removal of the `/do` GET bridge and the `token=` call
argument: no route, no `RIG_MODE`, no tool advertising a `token` parameter, a stray one ignored
rather than honoured, and — the one most likely to rot — **no instruction string that teaches
either**. That last gate exists because the copy in this file IS the product: deleting the routes
while leaving the paragraphs would produce an agent that follows our own documentation into a 404
and reports Vexa as broken.

The rig is stateless, so a token minted by `confirm_login` authenticates the *next* connection.
`?c=<token>` is the header-less spelling of the same one credential.

**Assert on the tool, not on the file.** The verbs are being split by owning domain; a test that
greps this file dies the day it moves, and a test that calls the handler follows it.
