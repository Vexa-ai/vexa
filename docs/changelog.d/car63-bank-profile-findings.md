- **An in-house model that needs no key runs chat turns.** With `models.runner: openai-agent` and
  `models.llmBaseUrl` (`VEXA_RUNNER` / `VEXA_LLM_BASE_URL`), agent-api no longer refuses turns with
  "No model credentials are configured", and no `Authorization` header goes to the endpoint.
  Leave `secrets.llmApiKey` empty for such an endpoint; a placeholder is no longer needed.
- **`helm upgrade` rolls the Pods that read a changed chart Secret.** Pod templates carry
  `checksum/*` annotations over the operator-supplied values they read (`secrets.*`,
  `flows.*`, the credential broker's Google client secret, `storage.s3` on admin-api, and a pinned
  `subjectTickets.<issuer>.signingKey` on its producer and the gateway). Generated
  secrets are not hashed, so a render with the same values rolls nothing.
- **The Terminal needs an `https://` public URL for sign-in.** The OIDC guide and the Enterprise
  section say so, and a production Terminal started with an `http://` `NEXTAUTH_URL` on a host
  other than `localhost` warns in its log. See
  [Sign-in with your own identity provider](/sign-in-oidc).
