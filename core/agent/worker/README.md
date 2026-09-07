# agent · worker

The agent worker: runs a single agent workload to completion. Owns the generic turn engine
(`engine`), the per-meeting loop (`meeting`, `meeting_transcript_mcp`), and its container image
(`Dockerfile`). Model/harness access goes through the provider-agnostic [`llm`](../llm) ports —
card beats via `CompletionPort` (a direct HTTP completion), workspace turns via `HarnessPort` (the
`VEXA_RUNNER`-selected CLI agent); no vendor name lives in this package. Spawned by the control
plane; liveness = workload lifecycle.

`jobs` is the background-job runner (`Vexa-ai/vexa#1584`): a marked act — Create, Extend, or
anything the model hands to `spawn_job` — leaves the serve loop at once and runs on its own thread
with its own harness session, so a two-minute act no longer holds the chat for two minutes. It lives
here rather than in an adapter because it sits ABOVE the harness and must be one implementation for
every runner. The contract is [`../llm/JOBS.md`](../llm/JOBS.md).
