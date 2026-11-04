- **Meetings depend on Flows for scheduling only (#1962).** On a deployment whose meeting-api has no
  scheduler (`VEXA_FLOWS_API_URL` unset), a booking with `auto_join` on is refused by name:
  `POST /meetings`, and a `PATCH` or `PUT …/intent` that makes a plan a booking, answer `422` with
  `code: scheduling_not_deployed` and the key to set. Calendar sync still imports the event, with
  `auto_join` off and `auto_join_error: "scheduling not deployed"`, and arms it again on the first
  sync once the key is set. A booking whose start window closed while Flows was down reads
  `auto_join_error: "not started: scheduler unavailable"`. A bot's
  lifecycle callbacks no longer wait on Flows: the `meeting.started` / `meeting.completed` facts are
  handed over in the background, so a hung Flows no longer adds up to 2 s to each step of a live
  meeting. See [Meetings API](/api/meetings#auto-join).
