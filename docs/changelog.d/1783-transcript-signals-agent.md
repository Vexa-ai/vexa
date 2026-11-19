- **Transcript signals: the agent decides what a meeting is listened for** (feature
  `transcript_signals`, policy `transcript_signals: off` by default). The meeting's agent writes a
  listening brief with `meeting_listen`: focus, labels, what it already knows, how sensitive the wake
  is, and the score from which a line is highlighted. The brief sits in the meeting page's front
  matter, where the people in the meeting can read it. `addressed_to_vexa` is always in it, and
  `wake_at` and `max_wait_s` stay inside company bounds.
  *How it works:* when the signals service has heard enough, it posts one `meeting.signal` to flows.
  The company flow `meeting_signal` (seeded in `_global`, the global account's to change or retire)
  wakes the agent in the meeting's chat (event.v1 `target.chat`). The turn queues behind any running
  turn there, and its reply appears in the chat as "From the meeting".
  *Why it complies:* what people say in a call is untrusted. A signal-started turn mounts every
  workspace read-only, and agent-api lets its tools write only that meeting's page and its brief.
  Anything else it must propose. Only the signals service can post a meeting signal. Wakes are
  capped per meeting (`signals_wakes_per_meeting`), and no model is called until the company names
  one (`model_signals`).
- **Rooms (feature `rooms`, off by default and not part of `all`).** Chats stay private: just you
  and Vexa. A room is made in a workspace. It is open by default: every member of that workspace sees
  it and can join it. A room can also be closed: only its members see it, and members invite others.
  In a room, a person's message does not start an agent turn: it goes to the room's log and to its
  sensor. `@vexa`, the agent's name, or a reply to the agent's message still starts a turn at once.
  Any member sets how proactive Vexa is (off, quiet, active), and the change is said in the room.
  When the sensor wakes the agent, the turn may answer in the room and propose, and nothing else.
  With the feature off, shared chats behave exactly as before.
