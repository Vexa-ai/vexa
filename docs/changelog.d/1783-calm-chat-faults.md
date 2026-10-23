- **A moment's unavailability no longer interrupts a chat (#1783).** When a message meets a brief
  outage before its turn starts — the gateway's sign-in check answering 503 with `Retry-After`, an
  edge 429, 502 or 504, an agent that is still being released — the chat sends the same message
  again by itself, under the same turn id so it runs once, waiting at least what `Retry-After` asks
  (1 s, 2 s, 4 s, 8 s, up to about 30 s), while the message shows its normal working state. Only if
  it still cannot get through does one muted line appear under it, "Couldn't reach Vexa just now",
  with Retry; the service and status code are under "details". Retrying replaces the failed attempt,
  so the transcript keeps one copy of the message. Fault lines and failed tool steps are drawn in
  neutral colours; red is kept for a security refusal.
