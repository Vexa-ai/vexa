/** Terminal mode — the build-time deployment profile of the workbench.
 *
 *  `NEXT_PUBLIC_TERMINAL_MODE=meetings` ships a MEETINGS-ONLY terminal: only the meetings list,
 *  the meeting/canvas tabs, and the API-tokens surface register; the agent surfaces (chat,
 *  workspace, routines, sessions) and their commands never register, and the server proxy
 *  refuses agent-api paths (see src/app/api/proxyMode.ts) so no agent traffic is possible.
 *  Unset (the default) keeps every surface.
 *
 *  NEXT_PUBLIC_* is inlined into the client bundle at BUILD time (like NEXT_PUBLIC_GA_MEASUREMENT_ID)
 *  — changing the mode requires a rebuild. Read via a function (not a module constant) so the
 *  server-side proxy and tests observe the env at call time.
 */
export function meetingsOnly(): boolean {
  return process.env.NEXT_PUBLIC_TERMINAL_MODE === "meetings";
}

/** `NEXT_PUBLIC_TERMINAL_MODE=minutes` ships the MINUTES terminal — the product a participant
 *  meets after clicking a link in an extract email. Three surfaces register and no more: the
 *  rooms list (a room's README is its index), the meetings list, and the right-rail chat scoped
 *  to the room in view. Routines, tasks, entity browsers, the token panels, the settings hub and
 *  the admin panel never register.
 *
 *  This is a SHAPE, not a fork: every surface is the same module the full workbench registers.
 *  A different product is a different registered set. */
export function minutesOnly(): boolean {
  return process.env.NEXT_PUBLIC_TERMINAL_MODE === "minutes";
}

/** True when the workbench is running a reduced product profile of any kind. */
export function reducedMode(): boolean {
  return meetingsOnly() || minutesOnly();
}
