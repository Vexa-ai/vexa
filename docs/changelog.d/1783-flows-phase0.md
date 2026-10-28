- **Routines do what they were made for, and say when they could not (#1783).** A routine you make
  in chat to do something that needs you — "send a bot to this meeting at 12:13" — now carries that
  act as a named grant, shown to you as "This routine may: send bots to meetings" and on its strip;
  company policy caps it (`routine_unattended_actions`, default `bot.request, bot.stop,
  flow.retire_self`) and `flow_lifecycle … revoke_grants` takes it back. A one-time routine uses
  `trigger: {kind: "at", at}` and retires after it fires. `request_meeting_bot` is a step of its
  own, so a bot at a time needs no agent run. A run whose acts were refused, or a one-shot that never
  sent its bot, ends `failed` with the reason and one line in its chat instead of `done`. See
  [Routines](/flows/routines#what-a-run-may-do).
