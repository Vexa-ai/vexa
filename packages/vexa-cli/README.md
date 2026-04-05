# Vexa CLI

Local terminal client for the Vexa agent runtime. Works like `claude` CLI but routes through ephemeral containers — persistent workspaces, sessions, scheduling.

## Install

```bash
pip install packages/vexa-cli/
```

## Setup

```bash
vexa config
# Endpoint: http://localhost:8100
# API key: vxa_bot_...
# User ID: 1
```

Or via environment variables:
```bash
export VEXA_ENDPOINT=http://localhost:8100
export VEXA_API_KEY=vxa_bot_...
export VEXA_USER_ID=1
```

Config stored in `~/.vexa/config.json` (600 permissions). Env vars override file.

## Usage

### Chat

```bash
# One-shot
vexa -p "what files are in my workspace?"

# Interactive REPL
vexa

# With model override
vexa -p "explain this code" --model sonnet

# Resume specific session
vexa --session abc123...

# Forward flags to claude CLI inside container
vexa -p "review" --flags "--effort high --permission-mode auto"
```

### Interactive commands

Inside the REPL (`vexa` with no args):

```
/reset           Start a new session
/rename <name>   Rename current session
/sessions        List sessions
/session <id>    Switch to session
/files           List workspace files
/cat <path>      Read workspace file
/status          Show connection status
/help            Show this help
/exit            Quit (or Ctrl+D)
```

Any unrecognized `/command` passes through to the agent.

### Sessions

```bash
vexa sessions                    # list all
vexa sessions --new "project X"  # create named session
```

### Workspace

```bash
vexa workspace ls                        # list files
vexa workspace cat notes.md              # read file
echo "content" | vexa workspace write f.md  # write file
```

### Status

```bash
vexa status   # endpoint, health, workspace, container state
```

## Architecture

```
vexa CLI  ──HTTP/SSE──>  agent-api:8100  ──docker exec──>  ephemeral container
  (local)                 (manages lifecycle)                 (claude CLI runs here)
                          (sessions in Redis)                 (/workspace/ persisted to S3)
```

The CLI is a thin SSE client. All logic lives in agent-api. The workspace is remote (in S3/container), not on the local filesystem.

### Flag passthrough

`--flags` forwards arbitrary flags to the `claude` CLI inside the container. When claude adds new flags, they work immediately — no vexa CLI update needed.

```bash
vexa -p "hello" --flags "--effort high --permission-mode auto --allowedTools Read,Grep"
```

Flags are shell-escaped (`shlex.quote`) server-side to prevent injection.
