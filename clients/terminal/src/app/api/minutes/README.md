# api/minutes

Server routes that exist only for the **Minutes** product shape
(`NEXT_PUBLIC_TERMINAL_MODE=minutes`). Each route guards on that mode and on `NODE_ENV`, returning
404 otherwise — so nothing here can exist in a deployment that did not ask for it.

- `seed/` — write a new room's README index at creation time.
