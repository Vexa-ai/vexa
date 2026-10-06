- **Per-user meeting length limit with an announced leave.** `zaki-control.v1` policy gains
  `max_meeting_minutes` (≤240): a capture clamps the bound to the platform cap and the metering
  reserve, and the bot posts a short in-meeting chat line before leaving at it
  (`user_limit_reached`). Status callbacks and `GET /captures/{id}` surface `limit_reached`.
