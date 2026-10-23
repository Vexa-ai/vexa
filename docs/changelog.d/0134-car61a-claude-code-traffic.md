- **Agent workers: Claude Code no longer calls its vendor for anything but the model.** The worker
  and Lite images set `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` in Claude Code's managed settings,
  so the CLI sends no telemetry or error reports and checks for no update. The Codex CLI is
  unchanged. See [No-egress clusters](/deployment-kubernetes#no-egress-clusters).
