- **Turning a routine on needs your signed-in session again.** `PATCH /api/routines/{name}/enabled`
  with `{"enabled": true}` answers `403 person_approval_required` to an API key and to an agent;
  turn a routine on from the app. Switching one off still works with a key.
- **The CRM's outbox posts with its own publish key.** `crm-outbox` sends `VEXA_FLOWS_CRM_PUBLISH_KEY`
  instead of flows' operator key. That key admits the CRM's own facts at `POST /events` and opens no
  other flows route. `make up` mints it and the chart generates it (`secrets.crmPublishKey`). With
  `secrets.existingSecretName`, add `VEXA_FLOWS_CRM_PUBLISH_KEY` to your Secret. On Compose, set it
  once in `.env`; flows-api and `crm-outbox` read the same value. The outbox now checks its keys at
  boot and does not start without them.
- **Every Terminal page carries its Content-Security-Policy**, including pages whose address ends in
  `.json`, `.txt`, `.svg` or `.png`.
- **A sub-millicore CPU size is refused at boot.** `RUNTIME_BOT_CPU` or `RUNTIME_AGENT_WORKER_CPU`
  between 0 and 0.001 would have declared a `0m` Pod; the runtime now does not start with it.
