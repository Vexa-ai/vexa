# `deploy/dogfood/rig/tests` — the rig's behavioural tests

Until 2026-09-03 this directory did not exist. `vexa_control_mcp.py` is 5,033 lines and 53 verbs —
the whole agent-facing surface — and **nothing called one of them**: the only file that touched it,
[`core/agent/tests/test_rig_stateless.py`](../../../../core/agent/tests/test_rig_stateless.py),
greps the source text. That is why the shell injection, the missing membership check, the
world-readable credential stores and an operator gate that broke `bot_schedule` were all invisible
to a green suite (review row R-D20).

## Run them

```bash
cd deploy/dogfood/rig && python3 -m pytest tests -q
```

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
| 4a·4b | R-D07 | `reaction_signal` is operator-only; `reactions_list` is scoped to the caller |
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

**Assert on the tool, not on the file.** The verbs are being split by owning domain; a test that
greps this file dies the day it moves, and a test that calls the handler follows it.
