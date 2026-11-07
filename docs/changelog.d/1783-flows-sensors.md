- **Sensors and keyed schedules (next sprint, ADR-0050) (#1783).** Make a calendar sensor from chat,
  test it on your real calendar with no side effects, switch it on, and connect it to a chat or a
  flow. A bot can go one minute before every meeting with a video link and follow reschedules, with
  no model call per meeting. An invitation to the shared mailbox arms one bot per meeting: an update
  moves it, a cancellation removes it, and the meeting goes into the workspace the invitation names.
  See [Sensors](/flows/sensors).
