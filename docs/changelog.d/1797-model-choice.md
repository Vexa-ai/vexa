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
