# zoom-sdk-capture/scripts

[`check-isolation.js`](check-isolation.js) — the brick's `gate:isolation` (P2) check.
`@vexa/zoom-sdk-capture` imports `@vexa/capture-codec` and Node builtins only. The native
runtime's capture port is injected, so the brick never imports the runtime, the join module or the bot.
