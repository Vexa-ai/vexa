# src — runtime kernel implementation

`runtime_kernel/` — the Python kernel that implements `contracts/runtime.v1`: spawn/execute workloads
through the lifecycle (`starting→running→stopping→stopped→destroyed`), emit `RuntimeEvent`s, over a
pluggable backend (process now; docker/k8s slot in behind the same port).
