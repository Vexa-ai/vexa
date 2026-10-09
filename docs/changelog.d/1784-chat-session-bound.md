- **A chat session id is bounded (#1784).** `POST /api/chat`, `/api/chat/submit` and
  `/api/chat/reset` take a `session` of 1–128 letters, digits, `.`, `_` and `-`, starting with a
  letter or digit, and answer `422` to anything else. Every id the terminal, flows and agent-api
  mint already fits. See [Agent API](/api/agent).
