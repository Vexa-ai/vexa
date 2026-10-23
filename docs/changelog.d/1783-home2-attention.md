- **The attention home's read model (#1783, feature `home2`, off by default).** agent-api answers
  `POST /api/desktop/attention` — per workspace you can open, what needs you (a proposal to accept, a
  mention, a card waiting on you, a call starting within a day, a routine of yours that failed), what
  is working there, how many rooms are active, the live call and the last change — and
  `POST /api/desktop/refs`, which says what each typed reference in a README is for you, or only that
  you cannot see it. Rules, not a model; nothing stored; your own app only. Contract: `desktop.v1`.
- **Room unread counts and run history on the workspace page (#1783, `home2`).** A room shows how
  many messages others wrote since you last read it (reading the room yourself clears it; an agent
  reading it for you does not), and a lock when the room is closed. A routine shows its recent runs.
