/** Is the catalogue served? Anywhere but a production build; in production only when the operator
 *  sets `VEXA_TERMINAL_DESIGN_CATALOGUE=1` (dev and staging do; production does not). */
export function catalogueEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.NODE_ENV !== "production" || env.VEXA_TERMINAL_DESIGN_CATALOGUE === "1";
}
