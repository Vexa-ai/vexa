# claude-creds-sync — keep the mounted Claude credential fresh

Agent containers bind-mount the host's `~/.claude/.credentials.json`
(`HOST_CLAUDE_CREDENTIALS` in `deploy/compose`) read-only to use a Claude
subscription for inference. Whether that file stays valid depends on the OS:

| Platform | Claude Code's credential store | What's needed |
|---|---|---|
| **macOS** | login **Keychain** — the file is a one-time export that expires every ~8–12 h | this sync daemon |
| **Linux** | the file itself — the CLI refreshes it in place | nothing |
| **Windows** | run the stack + CLI inside **WSL2** → Linux case | nothing |

Symptom of a stale file: agent chat fails with `401 Invalid authentication
credentials` even though `claude` works fine in your terminal (the CLI reads
the Keychain, the containers read the file).

## Install

```bash
deploy/bin/claude-creds-sync/install.sh            # install / update
deploy/bin/claude-creds-sync/install.sh uninstall  # remove
```

On macOS this registers the launchd user agent `ai.vexa.claude-creds-sync`
(every 5 min + at login) running `sync.sh`, which exports the Keychain item
into the file **only when it differs**, via same-inode truncate-write (a `mv`
would detach the container bind-mount). Refreshes are logged to
`~/.claude/creds-sync.log`. On Linux/WSL it just verifies the file exists.

Force a sync right now (macOS): `launchctl kickstart gui/$(id -u)/ai.vexa.claude-creds-sync`

Worst case after a reauth is a ≤5-minute window where a turn can still 401;
it recovers on the next turn.

The credential-file mount is the "developer's own machine" path. For a
portable, rotation-proof setup use an API key / custom endpoint via
**Settings → Models** instead (stored in Postgres) — see
`docs/docs/configuration.mdx` § Agent inference.
