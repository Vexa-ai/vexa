- **Chats are kept in the session store only, never in a workspace (#1783).** With the session
  store on, a turn that carries no conversation (a routine's run now carries its own thread) keeps
  its harness state in a private folder outside every mount, so nothing new lands in `_system`. The
  agent is told that chats live in the session store and reads them only with `chats_search` and
  `chat_read`; `_system` now says it holds identity and settings. `session_migrate --clean --retire`
  moves every old-layout chat file out of the workspaces into `<workspaces>/.sessions-retired/<run>/`
  after verifying the store's copy, including copies that diverged from it; `--restore` puts them
  back. A forget redacts those copies too.
