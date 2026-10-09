# agent · shared

Shared agent primitives used by both the control plane and the worker. Imported as `shared.<module>`;
nothing here starts a server.

| Module | What it is |
|---|---|
| `config` | the agent's settings, a validated config.v1 contract read from the environment (P14) |
| `models` | the agent domain's own Pydantic shapes |
| `ports` · `adapters` | the hexagonal seams (P5) and their real implementations: the git workspace, GitHub, the runtime and its scheduler over HTTP, the dispatch identity minter, the transcript stream reader, and the membership index and model config over admin-api |
| `core` | the agent-run core: transcript.v1 → governed action → a workspace commit |
| `units` | builds the one canonical unit.v1 dispatch envelope |
| `unit_input` | who may put a message on a live worker's input stream: the per-unit key agent-api signs each entry with and the worker verifies |
| `spawn` | builds the runtime.v1 `WorkloadSpec.env` for an agent worker |
| `delegation` | the worker's per-dispatch token (delegation.v1), vendored byte for byte from `core/identity/contracts/delegation.v1/` |
| `tools` | the generic toolbelt mechanism: tool.v1 → a Claude grant |
| `seeding` | materialises a person's workspace from a validated template; the "passes checks" gate for a seed folder |
| `governance` | the dormant hard-enforcement hook for workspace entity writes |
| `token_destination` | where a git token may travel: the one rule clone, pull and push obey |
| `atomic_json` | the one atomic JSON write: a private temp file beside the target, fsynced, then `os.replace` |
| `git_redaction` · `gitenv` | the P15 scrubber for git output, and the scrubbed environment every git subprocess runs with |
| `host_claude` | where this container reads the host's Claude subscription credential, resolved at read time |
| `timeline` | the worker's `now / last / next` block, from flows-api's read-only timeline route |
| `proposals` | the person's short list: what is worth doing now, written by whichever agent saw it |
| `friction` | the shape of the rough-edges record |
| `chat_label` | the one rule that names a chat |
| `marks` | the two chat marks, the literals three images agree on (fact-parity) |
| `desk_readme` · `desk_now` | the desk README as a hub of links, and its `Now` section built from dated facts |
| `meeting_doc` | a meeting's own page, with the live transcript in it, grown by Expand |
| `terms` | the words a meeting said that are worth a chip, and which already have a page |
| `page_images` · `asset_source` | an image on a page: checking its address, the guarded fetch that brings a remote one into the workspace, and the index of where it came from |

_Governed by `docs/docs/governance/architecture.mdx` (P1–P23)._
