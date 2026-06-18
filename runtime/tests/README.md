# tests — runtime kernel

`test_lifecycle.py` is the Stage-2 gate: drive a real workload through the full `runtime.v1` lifecycle
on the process backend, assert the legal transition sequence, and validate every emitted `RuntimeEvent`
against `contracts/runtime.v1/runtime.schema.json` (the impl conforms to the frozen contract).
