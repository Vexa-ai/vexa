- **Google and Microsoft sign-in require a verified email (#1784).** The terminal takes the address
  from Google only when Google reports it verified. For Microsoft, set `MICROSOFT_TENANT_ID` to your
  tenant id to admit that tenant's accounts; with `common`, a sign-in must carry the `xms_edov`
  optional claim (add it to the ID token in the app registration), or it is refused with a message on
  the sign-in card. The emailed sign-in link is unaffected. See
  [Kubernetes → sign-in providers](/deployment-kubernetes).
