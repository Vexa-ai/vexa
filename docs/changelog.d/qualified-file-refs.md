- **A file reference names its workspace.** A file is referenced as `[[page:<workspace>/<path>]]`.
  A bare path such as `README.md` in a chat is plain text and opens nothing. Before, it could open
  another workspace's file of the same name, such as the organisation's README. The agent writes
  qualified references, and write tools answer with the file's `ref`. Older replies are qualified only
  when their chat wrote into one workspace. See [Pages](/minutes/pages).
