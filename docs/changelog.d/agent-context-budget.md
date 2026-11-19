- **A chat keeps its history: the context budget is the model's window.** The openai-agent harness
  used to trim a chat's history to a 24,000-token default, dropping dozens of messages on a model
  with a much larger window. The default is now 131,072 tokens, and with a model catalog each model's
  own window applies. If a chat ever outgrows it, older history is compacted (old tool results
  first, then old tool exchanges, then old replies shortened). A person's messages and the latest
  exchange are never touched. The turn shows a muted "older context compacted" note in its activity
  line instead of a line under the reply. See [Agent tool calls](/agent-tool-calls).
