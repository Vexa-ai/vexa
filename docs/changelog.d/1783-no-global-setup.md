- **No setup step before a fresh instance works (#1783).** Everyone who signs in is served from their
  first sign-in, the first administrator included; nobody is held back, and flows send, while the
  company layer in `_global` is unwritten. `_global` may stay empty: agent-api creates it in the
  workspace store at boot, so a compose stack dispatches chat with nothing configured, and
  `VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH` is now optional. Mails read "the meeting assistant at this
  organisation" until a company name is written.
