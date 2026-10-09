- **openai-agent runs on your own model endpoint (#1784).** When Settings → Models points at your
  own endpoint, an openai-agent turn now runs there with your key, your model and your extra body,
  on any deployment. Before, a deployment that set `VEXA_LLM_BASE_URL` ran the turn on its own
  endpoint instead. Your key goes only to your endpoint, and the deployment's extra body is not sent
  there. See [Settings](/api/settings).
