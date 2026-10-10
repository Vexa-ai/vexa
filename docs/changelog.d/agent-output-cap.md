- **The agent's output-token cap is configurable: `VEXA_AGENT_MAX_OUTPUT_TOKENS`.** The claude
  CLI asks for 32000 output tokens on every request by default. A provider that prices that
  allowance before answering, such as OpenRouter, refuses a small balance with a 402, even for a
  one-word message. One setting now caps it for every harness. claude-code receives it as
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS` and openai-agent sends it as `max_tokens`. The runtime forwards
  it into every worker. Unset keeps each harness's own default. See
  [Configuration](/configuration).
