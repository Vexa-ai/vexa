/** Is the card lab served? Only outside a production build — there is no switch to turn it on there. */
export function labEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.NODE_ENV !== "production";
}
