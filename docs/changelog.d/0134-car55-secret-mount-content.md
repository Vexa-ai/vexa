- **A model API key no longer reaches an agent worker through a Secret mount under another name.**
  `RUNTIME_K8S_SECRET_MOUNTS` (`runtime.workerSecretMounts`) refused a mounted key by its name only,
  so an API key stored as `auth.json` reached the worker. Every worker Pod with a Secret mount now
  runs an init container, `vexa-secret-mount-check`, that reads each mounted key first and stops the
  Pod when it holds a model-provider API key (an `sk-` key) or a non-empty value for a model-key
  name, such as a Codex `auth.json` logged in with an API key. Subscription files pass: a Codex
  ChatGPT login and a Claude subscription's OAuth tokens. The runtime still has no right to read
  Secrets.
