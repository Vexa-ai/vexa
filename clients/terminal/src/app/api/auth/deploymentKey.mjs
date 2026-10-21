// The key a request WITHOUT a session acts as — one rule, for the REST proxies (proxyAuth.ts) and
// the relayed sockets (socketUpgrade.mjs, server.mjs).
//
// None. A visitor with no session cookie is anonymous, and the proxies send nothing upstream for
// them, so the gateway refuses them. The one exception is a deployment that switched ZERO-LOGIN on
// (`VEXA_TERMINAL_ZERO_LOGIN=true`): there every visitor who can reach the terminal acts as the
// deployment key (`VEXA_API_KEY`, else `VEXA_BOT_API_KEY`). That is a single-person box behind a
// closed port, never a shared one; Lite sets it for the box it runs on.

/** Is zero-login switched on for this deployment? Only the literal `true` (any case) is yes. */
export function zeroLogin(env) {
  return String(env?.VEXA_TERMINAL_ZERO_LOGIN ?? "").trim().toLowerCase() === "true";
}

/** The key an anonymous request acts as: the deployment key under zero-login, else "". */
export function anonymousKey(env) {
  if (!zeroLogin(env)) return "";
  return env.VEXA_API_KEY || env.VEXA_BOT_API_KEY || "";
}
