- **A fresh Helm install starts the chat agent without a second upgrade (#1783).** The session
  store's schema Job is now part of the release, named after a hash of its own spec, instead of a
  `pre-upgrade` hook that `helm install` never ran. Before, a first install left agent-api waiting in
  its `session-schema` initContainer and the MCP restarting until someone ran `helm upgrade`. It also
  runs under `helm install --wait` and Argo CD. An upgrade whose agent-api image changed creates a new
  Job and removes the old one; nothing to do on existing installs.
