- **An agent's tool access stays private to its own run (#1784).** A worker now keeps its Vexa
  tool-access token in a private directory outside every workspace, instead of in the workspace it
  works in, and removes one an older worker left there. No file, upload or move route reads, writes
  or lists a path under `.claude` in any workspace.
