- **A model picked mid-chat answers the next message (#1783).** A chat's agent keeps the model it
  started on, so a pick made while it ran used to leave every later message on the old model. The
  next message now starts an agent on the pick; until then the composer names the model that is
  answering and says "switching to … on your next message". A person an operator named on a Claude
  model starts new chats on it; `_global/POLICIES.md` `model_chat_default: catalog` keeps the
  catalog's default for everyone.
