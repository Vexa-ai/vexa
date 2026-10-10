- **Mail relay credentials are sent only over TLS (#1784).** The terminal's sign-in mail and flows
  now use STARTTLS whenever the relay offers it (a provider's :587), with the certificate checked, as
  well as implicit TLS with `VEXA_MAIL_SMTP_SECURE=1` (:465). With `VEXA_MAIL_SMTP_USER` and
  `VEXA_MAIL_SMTP_PASSWORD` set, a relay that offers neither is refused and nothing is sent. A
  login-free relay such as the dev mail double still works over plain SMTP. See
  [Configuration](/configuration).
