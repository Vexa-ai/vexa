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
