- **Run the whole product on your own model, with nothing leaving the estate (#1417).** The
  workspace agent's harness speaks the Anthropic `/v1/messages` dialect; a self-hosted vLLM, Ollama
  or llama.cpp server speaks only the OpenAI one. `docker compose --profile llm-shim up -d llm-shim`
  starts the missing front door between them. Off by default — a hosted gateway that already serves
  both dialects needs nothing. Per-account, `PUT /user/models` now takes `extra_body` (server-specific
  request fields the OpenAI dialect cannot express, as a JSON string) and `runner` (which harness runs
  your turns). See [Settings](/api/settings) and `deploy/compose/llm-shim/README.md`.
