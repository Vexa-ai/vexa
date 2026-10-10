- **A model provider's 402 is shown once, as a typed fault.** When a provider refuses a turn for
  lack of credit, the chat now shows one block: `Model provider · out of credits (402)`, the
  provider's own explanation, and a remedy line. The remedy names the output cap when the provider
  says the balance covers a smaller answer. The provider's raw `API Error` text no longer appears
  as the agent's words or as a second "Model inference failed" line. Links in the provider's
  message, such as its key-management page, stay out of the chat and go to the worker's log for
  the operator.
