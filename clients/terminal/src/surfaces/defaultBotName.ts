/** Default bot name for terminal join requests.
 *
 *  Returns `undefined` when unset so `JSON.stringify({ bot_name: defaultBotName() })` omits the field
 *  and meeting-api names the bot from `DEFAULT_BOT_NAME` — the knob operators can change with a
 *  restart. An explicit `bot_name` in a request still wins over both.
 *
 *  `NEXT_PUBLIC_DEFAULT_BOT_NAME` is inlined into the bundle at build time (the Dockerfile takes it as
 *  a build arg), so setting it pins a name into the image and makes a rename cost a rebuild.
 */
export function defaultBotName(): string | undefined {
  const name = process.env.NEXT_PUBLIC_DEFAULT_BOT_NAME?.trim();
  return name || undefined;
}
