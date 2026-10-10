- **Pick the model a chat runs on (#1796).** Operators declare a model catalog
  (`VEXA_MODEL_CATALOG`, or `models.catalog` in Helm). A catalog can offer:
  - a self-hosted Qwen 3 or any other OpenAI-compatible endpoint;
  - any model on OpenRouter;
  - Anthropic, by API key or by the deployment's subscription;
  - each person's own Settings → Models endpoint.

  Each chat then has a model picker in its composer that lists only the models that person may use.
  A pick takes effect on the chat's next message. A person's default model, and the organisation's,
  is a Settings → Models field. Credentials appear in the catalog only as `secret_ref` references,
  and each goes only to its own provider. A catalog with anything wrong in it is refused at boot,
  with every problem named. A chat whose model cannot run is refused with a typed fault and never
  runs on another model. Without a catalog, nothing changes. See [Model catalog](/model-catalog).

- **Pick an effort level per chat, where the model offers one (#1796).** A catalog entry can list
  the effort levels it offers (`capabilities.reasoning_efforts`, with an optional
  `default_effort`). An effort selector then appears beside the model chip. Each adapter sends the
  level in its provider's own field: OpenRouter's `reasoning.effort`, the OpenAI
  `reasoning_effort`, a Qwen's `enable_thinking` switch, or the claude CLI's `--effort`. A level the
  adapter cannot send is refused at boot, and a level the model does not offer is refused at pick
  time with a typed fault. A catalog entry can also set its own `max_output_tokens`. See
  [Model catalog](/model-catalog#effort-levels).
