# agent · worker

The agent worker: runs a single agent workload to completion. Owns the decision engine (`engine`, `decision_claude`), the per-meeting loop (`meeting`, `meeting_transcript_mcp`), and its container image (`Dockerfile`). Spawned by the control plane; liveness = workload lifecycle.
