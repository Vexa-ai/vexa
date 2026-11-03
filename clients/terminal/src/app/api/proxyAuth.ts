/** Per-user API key for the upstream proxies.
 *
 *  The X-API-Key the terminal forwards to the gateway / agent-api is the signed-in person's token,
 *  read from the httpOnly `vexa-token` cookie (set by /api/auth/login). A request with no cookie is
 *  anonymous and is forwarded with no key, so the gateway refuses it — unless the deployment
 *  switched zero-login on, where it acts as the deployment key (`auth/deploymentKey.mjs`, the one
 *  rule the relayed sockets read too). */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "./auth/adminApi";
import { anonymousKey } from "./auth/deploymentKey.mjs";

/** Resolve the API key to send upstream: the cookie token, else the anonymous key (empty unless
 *  zero-login is on). cookies() can throw outside a request scope (e.g. in tests); that request is
 *  anonymous too. */
export async function resolveApiKey(): Promise<string> {
  let cookieToken: string | undefined;
  try {
    cookieToken = (await cookies()).get(AUTH_COOKIE)?.value;
  } catch {
    cookieToken = undefined;
  }
  return cookieToken || anonymousKey(process.env);
}
