### Private data stays out of shared surfaces, and the hold survives restarts (0.13.4 S1, D6)

- When a chat reads the person's CRM, transcripts, mail, calendar or person-wide views, that session
  is marked. While it is marked, its writes into anything other people read are held: pages, file
  names, a workspace's name or purpose, messages into a shared conversation, room messages, notices,
  CRM writes and card proposals. The person releases one write with `confirm <code>`.
- The mark is stored in Redis, so a restart, a second replica and a worker's idle exit keep it. When
  it cannot be read, the write is held with the typed fault `held_private_egress`.
- Held content is encrypted in agent-api and is no longer stored on the person's desk, where a
  post-meeting run could read it. The person can list and open held items (`GET /agent/held`).

How it works: every read route labels its answer `X-Vexa-Privacy`. The gateway, and the MCP for
the tools it calls directly, marks the worker's unit and conversation from that label. An answer
with no label counts as private, and a mark that cannot be written refuses the read.
Why it complies: the decision rests on labels the services set and holds the services enforce, not
on a model's judgement. It fails closed at every step, and nothing held is kept on a mount.
