- **Helm: a meeting bot can burst above its CPU request.** `runtime.workloadResources.meetingBot.cpuLimit`
  (runtime env `RUNTIME_BOT_CPU_LIMIT`) sets the bot Pod's CPU limit above its `cpu` request, so a bot
  uses a node's idle cores when its browser needs more than one reservation, while the scheduler
  still packs bots on the request. The bot Pod becomes Burstable instead of Guaranteed. Unset, the
  limit equals the request and the Pod is unchanged. A limit below the request, or without one,
  stops the runtime at boot.
