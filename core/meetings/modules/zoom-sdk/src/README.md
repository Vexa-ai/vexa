# Source

The root is types-only. `node.mjs` owns the child lifetime; `worker.cjs` invokes the external addon. `protocol.cjs` rejects malformed IPC. No capture pipeline dependencies.
