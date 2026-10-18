# flows_steps — step implementations

The TOOLS a flow names: one function per capability, registered by `__name__`. Real adapters call
domain HTTP APIs (meeting-api, agent-api, the notifier) and are OUTSIDE the engine's import graph;
`fakes.py` mirrors the same names against `FakeWorld` so every fixture and the storm run with zero
domains attached. A step answers `Done(result)`, `Wait(seconds|until)`, or `Block(reason, deadline)`
— nothing else; effects go through the receipt the engine reserved for it.

`mail_transport.py` is the mail wire both directions share: the IMAP endpoint (`VEXA_MAIL_IMAP_*`,
Gmail being the preset), the relay's TLS policy and CA bundle, and `MailTransportError` — the typed
fault (`mail:<auth|tls|connect|config|protocol>`) every IMAP and SMTP failure leaves as. It is a
`StepError`, so a send that fails inside a step lands on the reaction as its reason, with
`retryable` set by kind. `emailx.py` sends through it; `flows_integrations/inbox.py` reads through it.
