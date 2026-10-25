- **A model key that names no model now reaches only its harness's own models.** An agent turn on a
  model-provider key with no model named used to be allowed any model the key's endpoint served. It is
  now held to the family its harness speaks: Anthropic's models for Claude Code, OpenAI's for Codex
  and openai-agent. A request for another model is refused with `model_outside_scope`. The admin
  changes this with `model_scope_unpinned` in `_global/POLICIES.md`: `any` restores the old
  behaviour, and a list of model ids allows exactly those. Turns whose model is named are unchanged.
  See [Configuration](/configuration).
