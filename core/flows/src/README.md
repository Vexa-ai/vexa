# src

Three packages, one dependency direction: `flows` (the engine) ← `flows_steps` (the tools) ← `flows_defs` (the flows). See each package README.

`flows_obs.py` is the domain's `logevent.v1` emitter (stdlib only), shared by its three processes —
`flows-api`, `flows-worker`, `mailbox`. A trace enters flows-api on `X-Trace-Id` (or is minted), is
minted per unit of work in the worker (one step) and the mailbox (one message), and leaves on every
Vexa call through `flows_steps.common.http`. `session` comes only from identity's `delegation.unit`.
