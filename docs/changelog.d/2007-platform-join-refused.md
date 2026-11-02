- **New completion reason `platform_join_refused` (#2007).** A Google Meet meeting whose every bot was
  refused by Meet before any host saw a request now ends `failed` with `completion_reason`
  `platform_join_refused`, instead of `awaiting_admission_rejected`. The host never decided anything,
  and the failure is counted as Vexa's. Clients that switch on `completion_reason` see one new value.
