- **A queued message is your own pending bubble, and Send now works.** A message sent while the agent
  is answering shows as your bubble, dimmed, with its whole text, a small "Queued · after the current
  reply" label and three buttons: **Send now** (stops the current reply and runs this next), **Edit**
  (takes it back into the box) and **Remove**. When the agent takes it, the bubble stays where it is.
  Send now had done nothing: the agent's worker was not allowed to read the request; it is now. New
  agent-api route `POST /api/chat/pending/withdraw`.
