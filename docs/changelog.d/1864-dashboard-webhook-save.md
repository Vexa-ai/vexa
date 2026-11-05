- **Saving a webhook from the dashboard works again (#1218, #1864).** The dashboard's webhook page
  saves the URL, signing secret and events, and rotates the secret, through the same
  `PUT /user/webhook` the API uses; it used to fail with "Failed to save webhook config" and store
  nothing. See [Webhooks](/webhooks).
