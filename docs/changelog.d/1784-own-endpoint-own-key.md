- **Your own model endpoint runs with your own key (#1784).** On the claude-code harness, a custom
  endpoint under Settings → Models now needs its API key. Without one the endpoint is not used,
  your turns run on the deployment's model, and the Test button says why. The openai-agent harness
  still runs on a keyless endpoint. On your own endpoint the agent holds no model credential but
  yours. See [Settings](/api/settings).
