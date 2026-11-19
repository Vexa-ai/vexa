- **"Call Maya" from your chat starts a Vexa call.** Vexa finds the person among the people you share a
  workspace with, opens your room of two, starts a Vexa call there and rings them in the app
  (`call_person`, `POST /api/rooms/call`). A name that matches several people, or nobody, gets one
  short question. The agent no longer answers that Vexa cannot place a call, and no longer offers a
  Meet link instead. See [Rooms](/minutes/rooms).
- **The call opens for the caller.** After "Call Maya", the room opens and you are already in the call,
  shown as calling Maya with Cancel until she joins. It no longer ends unanswered because the room was
  not opened within 30 seconds.
