# deploy/bank-profile/stub — the bank's services, and the person, in stdlib Python

- `stub.py <mode>` — `model` (OpenAI-compatible `chat/completions` with tool calling and SSE),
  `stt` (an audio/transcriptions endpoint), `idp` (OIDC with RS256 ID tokens that carry no
  `email_verified`, PKCE checked), `scc` (an admission webhook doing what OpenShift's
  `restricted-v2` SCC does). Each serves TLS from a certificate the profile's corporate CA issued.
- `journey.py <step>` — the person's side: `signin` (the terminal's OIDC door, cookies and all),
  `workspace`, `chat`, `meeting`. Prints `RESULT <step> PASS|FAIL …` and exits 0 only on PASS.

Both run in a stock `python:3.12-slim` from a ConfigMap; nothing is built.
