# recording/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/recording` is a Node brick: only `playwright` (the MediaRecorder `Page` bridge) + Node builtins
(events/child_process/fs/http/https/crypto) — never another brick's internals, never the bot/service.
The chunk sink + loggers are injected, not imported.
