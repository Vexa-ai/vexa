- **Flows can no longer act as any person on any route (security).** The flows services held
  `INTERNAL_API_SECRET` and the admin key, so a compromised or faulty flows process could mint a
  token for any user and use it on calendar, mail and every other route. Flows now holds one key,
  `VEXA_FLOWS_SERVICE_KEY`. That key opens the gateway's new **service door** on port 8002, which
  is never published. The door forwards only the calls flows makes, from a fixed list, and refuses
  everything else with `403`. Calls to meeting-api carry the person's identity, signed by the
  gateway. The signals service and the terminal now reach flows-api through the door, so flows
  never receives the internal secret.
  **Upgrade:**
  - Compose: set `VEXA_FLOWS_SERVICE_KEY` in `.env`; `make up` mints it. Remove
    `VEXA_FLOWS_ADMIN_KEY` and `VEXA_FLOWS_USER_KEY_TTL_S`.
  - Helm: the chart generates `secrets.flowsServiceKey`. With `secrets.existingSecretName`, add the
    key `VEXA_FLOWS_SERVICE_KEY`.

  See [Security & compliance → The flows tier's credential](/security-compliance#the-flows-tiers-credential).
