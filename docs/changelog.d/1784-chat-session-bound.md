- **A chat session id is bounded (#1784).** Every agent-api door that takes a chat `session` —
  `POST /api/chat`, `/api/chat/submit`, `/api/chat/reset`, the pending list, the history path, chat
  naming, the chat target and the rail order — takes 1–128 letters, digits, `.`, `_` and `-`, starting
  with a letter or digit, and answers `422` to anything else. Every id the terminal, flows and agent-api
  mint already fits. See [Agent API](/api/agent).
