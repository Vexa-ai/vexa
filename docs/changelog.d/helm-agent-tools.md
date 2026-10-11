- **Helm: the chat agent's model route is a chart value, and a model without tool calling says so.**
  `models.runner`, `models.llmBaseUrl`, `models.llmExtraBody` and `secrets.llmApiKey` set the
  openai-agent harness on both agent-api and the runtime, which passes them to every agent worker. With
  them, the agent on a Helm install sends the bot, reads the transcript and stops the bot through its
  vexa tools. A mirrored registry pinned with `global.imageTag` now also supplies the agent-worker
  image; it was always pulled from `vexaai/`. A model or endpoint that does not do tool calling ends
  the turn with the typed fault `model-provider` / `no_tool_calling` instead of a reply saying the
  agent has no tools. See [Agent tools on Kubernetes](/deployment-helm-agent-tools).
