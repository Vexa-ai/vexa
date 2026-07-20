/** Default bot name shown in the terminal surfaces.
 *
 *  NEXT_PUBLIC_DEFAULT_BOT_NAME sets the meeting bot name the terminal sends to the API
 *  when joining meetings. In client-bundled code Next.js inlines NEXT_PUBLIC_* at build time,
 *  so the knob takes effect per deployment build — which matches this feature's intent
 *  (a per-deployment default), and the call-time function keeps tests correct.
 *
 *  Read via a function (not a module constant) so tests that set the env after load observe it.
 */
export function defaultBotName(): string {
  return process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME?.trim() || "Vexa";
}
