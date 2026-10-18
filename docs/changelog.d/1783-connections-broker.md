- **Connections run on a standard install (#1783).** The credential broker behind Connections is now
  part of the product: Docker Compose and the Helm chart deploy it beside agent-api, with its own
  image (`vexaai/v012-credential-broker`), keys generated on first start, and a network boundary
  that admits only agent-api and the terminal. Credentials are encrypted with AES-256-GCM under a
  key kept apart from the broker's volume; an OpenBao you already run can be used instead. Gmail and
  Calendar need a Google OAuth client; custom-service connections work without one. Vexa Lite does
  not include Connections. When a connection form was prepared by the agent, it now leads with the
  host the secret will be sent to, flags an unfamiliar host, and asks you to type the host before the
  first save. See [Connections](/connections).
