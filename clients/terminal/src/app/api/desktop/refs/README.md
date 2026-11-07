# terminal/src/app/api/desktop/refs — `POST /api/desktop/refs`

A page's typed references (`room`, `chat`, `call`, `file`, `person`, `card`, `routine`, `workspace`),
answered for the reader by agent-api's `desktop/refs` (#1783, R3, feature `home2`). A reference the reader
may not see comes back as `{ kind, visible: false }` and nothing else. At most 100 per page; crm-api's desk
is read only when a card is asked for.
