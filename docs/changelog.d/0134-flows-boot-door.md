- **flows waits for the gateway at first boot.** In compose, flows-api, flows-worker and
  flows-mailbox start once the gateway is healthy, since its service door is their only way to the
  other domains. The worker also reads `_global/POLICIES.md` before its first step, retrying for up
  to a minute, and logs what it heard. Before, a worker that started first ran under the closed
  policy rules and logged only a swallowed warning.
