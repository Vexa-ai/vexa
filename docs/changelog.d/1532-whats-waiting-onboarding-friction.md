- **`whats_waiting`, onboarding and friction reporting over MCP (#1532, #1545).** An agent asks
  `whats_waiting` at the start of a session and gets what its person's Vexa needs right now, each
  item carrying the sentence to say; a person with no meeting yet gets a first step instead of an
  empty queue. `report_friction` files what did not work — no field is required and no value a
  caller can send is refused — and `friction_so_far` reads your own reports back. See
  [Authoring workflows](/flows/authoring).
