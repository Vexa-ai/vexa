# `vexa_mcp/tools` — 64 tools, one module per domain

The domain is **the service the tools forward to**, which is why the split is not cosmetic:
`tests/test_thin_forward.py` asserts a tool touches at most one service door, so a tool in the wrong
module is a failing test rather than a comment nobody reads.

| module | tools | forwards to |
|---|---:|---|
| `identity.py` | 5 | admin-api — sign in, claim, the account behind a token |
| `meetings.py` | 18 | the gateway — bots, transcripts, seeds, participants, search, recordings (and `transcript_terms`, which goes to agent-api because the pages are there) |
| `workspaces.py` | 21 | agent-api — the knowledge, the claim book, the settings, attach/pull/push, members and invites |
| `flows.py` | 10 | flows-api — facts, reactions, lifecycle, timeline (and `whats_waiting`, which goes to agent-api because the queue is assembled there now) |
| `friction.py` | 4 | agent-api — the loop that turns a rough edge into a fix |
| `rehearse.py` | 3 | the `rehearse` package — user states as data |
| `panel.py` | 1 | nothing: `deeplink` composes a URL and reaches no service |
| `docs.py` | 2 | docs.vexa.ai — the two tools that answer with no account |

Two exceptions are named in the table above rather than hidden, and both are the rule working: the
tool goes where the DATA is, not where the noun sounds like it belongs.

## Adding one

Decorate a module-level function with `@tool`, and `@anon_guard` if it needs an account. The
docstring is the tool's description — it is what an agent reads to decide whether to call it, so
write it for that reader. Then run `uv run pytest -q`: if the body reaches past HTTP, fans out
across services, writes outside `VEXA_HOME`, or carries product copy, the suite says so and says
why.
