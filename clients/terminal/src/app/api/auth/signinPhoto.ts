/** A sign-in tells identity about the person's photo (UI decision 15g, Vexa-ai/vexa#1783).
 *
 *  Identity (admin-api) is the one writer of person photos: it fetches the provider's picture through
 *  its outbound-URL guard, checks that it is a PNG, JPEG or WebP of at most 200 KB, stores it, and
 *  re-checks it at each later sign-in. The terminal only says what this sign-in carried:
 *
 *    - Google, OIDC: the sign-in's raw ID token. identity verifies it itself (issuer keys, issuer,
 *      audience, expiry, and that its subject is the identity bound to this account) and only then
 *      reads `picture` from it. The terminal never names a picture address.
 *    - Microsoft: the sign-in's own access token (the scope the app has today, nothing added).
 *      identity asks Graph who the token is and fetches the photo only for the Microsoft identity
 *      bound to this account. The token is used for that one call and never stored.
 *
 *  Never image bytes: identity fetches every picture itself, so no caller can hand it one.
 *    - an emailed link: nothing (identity may check the person's own Gravatar).
 *
 *  It never holds up or refuses a sign-in: the caller does not await it, and every failure is quiet.
 */
import { putSigninPhoto } from "./adminApi";

type Claims = Record<string, unknown>;

export interface SigninPhoto {
  provider?: string;
  id_token?: string;
  access_token?: string;
}

const PROVIDERS = new Set(["google", "microsoft", "oidc"]);

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** The body for identity's `PUT /internal/users/{id}/signin-photo`, from what NextAuth handed the
 *  sign-in callback. Anything not plainly a picture is left out rather than guessed at. */
export function signinPhotoBody(provider: string | undefined,
  input: { user?: Claims | null; profile?: Claims | null; account?: Claims | null }): SigninPhoto {
  const p = str(provider);
  if (!PROVIDERS.has(p)) return {};
  if (p === "microsoft") {
    const token = str(input.account?.access_token);
    return token && token.length <= 8192 ? { provider: p, access_token: token } : { provider: p };
  }
  const idToken = str(input.account?.id_token);
  return idToken.split(".").length === 3 && idToken.length <= 16384 ? { provider: p, id_token: idToken } : { provider: p };
}

/** Tell identity, through admin-api's internal tier (`adminApi.putSigninPhoto`). Resolves true when
 *  identity answered 200; never throws. */
export async function captureSigninPhoto(userId: string | number, body: SigninPhoto): Promise<boolean> {
  try {
    return await putSigninPhoto(userId, body as Record<string, string>);
  } catch {
    return false;
  }
}
