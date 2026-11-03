- **The agent's sense of now names its offset (#1783).** Every place a turn learns the time (the
  timeline block, the `<schedule>` block and `current_time`) now says it the same way, for example
  "Tuesday 13 October 2026, 19:06 Europe/Lisbon (WEST, UTC+01:00); 18:06 UTC", and the block is
  re-dated on every turn rather than once a minute. Tool timestamps are UTC; with the offset beside
  the local time the agent no longer mistakes them for local time.
- **A chat turn that runs out of tool calls says how far it got (#1783).** It replies with what it
  did and what is left, and **Continue** starts the next turn with the remaining work. The budget is
  the `_global/POLICIES.md` key `chat_turn_max_tool_calls` (default 40); a workspace or a person may
  set a lower value, never a higher one.
