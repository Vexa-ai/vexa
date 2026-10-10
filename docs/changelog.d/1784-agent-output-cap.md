- **The agent's output-token cap is configurable: `VEXA_AGENT_MAX_OUTPUT_TOKENS` (#1784).** The
  Claude CLI asks for 32000 output tokens per request by default, and a provider that prices that
  allowance before answering refuses a small balance with a 402 even for a one-word message. The
  setting caps it for the claude-code harness (as `CLAUDE_CODE_MAX_OUTPUT_TOKENS`) and the openai-agent
  harness (as `max_tokens`); the runtime forwards it into every worker. Unset keeps each harness's own
  default. See [Long agent turns](/agent-tool-calls).
