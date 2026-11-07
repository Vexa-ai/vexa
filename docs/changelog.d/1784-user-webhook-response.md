- **`PUT /user/webhook` answers with the webhook config (#1784).** The same shape `GET /user/webhook`
  returns — URL, events, whether a secret is set — instead of the account record. A `webhook_url`
  naming an internal destination is refused with `422` when it is saved. See [Webhooks](/webhooks).
