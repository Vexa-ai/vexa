# api/minutes

Local-development seams for the **Minutes** product shape (`NEXT_PUBLIC_TERMINAL_MODE=minutes`).
Each route file is `route.dev.ts`: only the development server treats it as a route
(`next.config.ts` `pageExtensions`), so a production build contains none of them. On the development
server each still answers 404 unless `NODE_ENV=development` and minutes mode, and the ones that act
for a person (`ensure-meeting`, `person-state`) take the person from the validated session cookie,
never from the display-only `vexa-user-info` cookie.

- `seed/` — write a new room's README index at creation time.
