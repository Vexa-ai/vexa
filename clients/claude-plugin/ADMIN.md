# Vexa Live — for administrators

Vexa Live is a Claude Code plugin with a hooks module and no server of its own.

| Piece | Runs where | What |
|---|---|---|
| Band, listener | the person's machine, inside Claude Code | `hooks/register.tsx`, `hooks/listener.ts` |
| Vexa itself | your Vexa (hosted or your own) | reached through the MCP server the session already has: the claude.ai connector, the `vexa` plugin's server, or one an administrator declared |

**Network calls.** None of its own. It calls the person's Vexa MCP server through Claude Code's
connection (`$.mcp.call`), so whatever governs that server — `allowedMcpServers`,
`deniedMcpServers`, managed servers — governs Vexa Live too. Its model calls (filter, triage) go
through Claude Code's own model access, only while the person has pressed **Listen**.

**Data.** Transcript lines of the call being listened to reach the model the session already uses.
On the machine: Claude Code session state and the listener presets in `~/.vexa/apps/<name>`.
Your Vexa records that the plugin is installed (version, last seen) and when it first woke Claude,
where it offers `live_heartbeat`.

**Turn it off for everyone:** don't list the marketplace in `strictKnownMarketplaces`, or disable the
plugin in managed settings.
