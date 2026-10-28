# Vexa Live — background listening for Claude Code

The optional half of Vexa for Claude. Vexa itself (sending a bot, transcripts, calendar, workspaces)
comes from your Vexa connector or the `vexa` plugin. Vexa Live adds what an MCP server alone can't:
while a call of yours is live, a band above the prompt offers to listen; after **Listen**, a small
model reads each new piece of the transcript in the background and wakes Claude only when someone
says «Claude» or something matters. Without it, Claude can follow a call only inside one reply.

- **How it listens:** the `vexa-listen` skill has Claude get a short-lived, transcripts-only key
  (`listen_token`) and start `bin/vexa-listen` in the background. The script waits on Vexa's
  `wait_for_signal`, exits when someone says «Claude» (or the call ends), and Claude Code wakes
  Claude to answer and restart it. Each read tells Vexa the call is being listened to, which is what
  the call panel shows. It needs only `sh` and `curl`, so it works in the terminal and the desktop app.
- **No server of its own, nothing to sign in.** It finds your Vexa among the MCP servers this
  session already has (the one serving `get_meeting_transcript` and `whats_waiting`) and calls it
  with Claude Code's own connection.
- **Model calls:** only while you have pressed **Listen**, through Claude Code's own model access:
  the filter (haiku) on each new piece, the triage (sonnet) on signals that aren't a direct address.
  `listener_configure` tunes the rules and models; `listener_calibrate` measures them.
- **What it tells your Vexa:** that it is installed (a heartbeat every 5 minutes with its version)
  and when it first woke Claude, where your Vexa offers `live_heartbeat`.

Install (Claude offers this when it would help; it always asks first):

```
claude plugin install vexa-live --marketplace Vexa-ai/vexa
```

The desktop app loads it at once; in the terminal type `/reload-plugins`.

Then type **`/vexa`**. It opens the Vexa panel beside the chat, starts the line from your Vexa
(`bin/vexa-link`, one per machine) when none runs, and listens to a live call of yours in this
chat. `/vexa stop` stops listening here. If this chat can't reach your Vexa yet, `/vexa` (or
`/vexa signin`) opens the sign-in, and carries on by itself once you're in.
