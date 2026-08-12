- **Configuring Vexa over the API is documented (#1091).** New [Settings API](/api/settings) page
  covers the self-serve `/user/*` routes that were shipped but unwritten: model credential
  (`/user/models`), transcription backend (`/user/transcription`), webhooks and their delivery
  history (`/user/webhook`, `/user/webhook/deliveries`), with the field validation, the
  partial-update-and-clear semantics, and the rule that a stored secret is never echoed back in the
  clear. It also states the thing the path name invites you to assume and which is not true: there is
  **no** aggregate `GET /settings`, and the deployment-wide admin tier is not on the public API at all.
