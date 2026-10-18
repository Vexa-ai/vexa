# api/minutes/seed

**Local-dev seam.** Writes the seed README into a freshly created room — its purpose line and the
attention list the person gave during the room-creation conversation, so the room is born knowing
what it is for.

## Why a route rather than an agent turn

The product path for workspace writes is an agent turn. On a laptop the agent runner may have no
model credential yet (the BYOT decision is open), and a room whose index is empty until someone
configures a model is a bad first impression. This route writes the file directly instead.

**It is not a shipped capability.** It returns **404** unless `NODE_ENV=development` **and**
`NEXT_PUBLIC_TERMINAL_MODE=minutes`. When the agent writes room seeds, this deletes — it does not
get promoted.

## How it writes

`docker exec` into the agent-api container, content passed as base64 in argv (promisified
`execFile` has no stdin, and base64 sidesteps every shell-quoting hazard in user-authored text),
then a git commit in the workspace so the seed travels with the room like any other content.

`wsId` is validated against `^[A-Za-z0-9_-]+$` before it reaches a shell.
