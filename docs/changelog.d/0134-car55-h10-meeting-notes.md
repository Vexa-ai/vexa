- **Meeting-page commits made for an agent say so.** Binding a page to its meeting
  (`meeting_attach_page`) or minting a meeting's page through `/api/meeting/note` on an agent's
  request, and the note a room's call gets when its signal wakes the room's agent, now commit with
  `On-behalf-of` and `Written-by: agent` trailers (plus `Agent-unit` when the identity names one).
  The same acts done by the person carry none.
