- **Calls: stopping and starting a call again in the same chat transcribes the new call.** When a call's
  meeting ends, its LiveKit room ends with it instead of living on, so the next start gets a new
  meeting and listener. The agent's `[vexa-quiet]` marker never renders in a chat.
- **Routines: the post-meeting grounding check reads a transcript in any script**, so a report on a
  Russian or Portuguese meeting is no longer refused as ungrounded, and a run whose write is held for
  approval ends `done`, not `failed`.
