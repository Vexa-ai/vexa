- **Self-hosted deployments can opt into an external service authority without importing hosted
  billing policy (#988).** Meeting-api can now ask a signed, versioned authority before bot spawn
  and at each one-minute active-service boundary; stock OSS remains explicit allow-all when the
  integration is unset. See [Configuration](/configuration#optional-service-authority).
