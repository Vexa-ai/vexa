# bin

`vexa-listen`: the listener script Claude starts in the background (sh + curl). It waits on Vexa's `wait_for_signal` with a `listen_token` key and exits on the first line that names Claude, or when the call ends. Usage is in its header.
- `vexa-link` — the one line from the person's Vexa for the whole chat: their calls' events (`GET /link/events`) and the panel's presses (the Claude relay) appended to `~/.vexa/link/events.jsonl`, which the plugin's listener reads.
- `vexa-pair` — the first run's setup panel address with this machine's pairing code (`~/.vexa/pair`), which `listen_token` takes as `pair` to sign that panel in.
