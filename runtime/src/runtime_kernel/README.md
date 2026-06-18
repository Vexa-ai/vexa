# runtime_kernel — the kernel

Conforms to `runtime.v1`. Files: `models` (the v1 shapes as Pydantic, validated against the schema in
tests) · `backend` (the Backend port) · `process_backend` (spawn a child process) · `kernel` (the
lifecycle orchestrator + the opaque-profile registry, P11). Depends on nothing above it.
