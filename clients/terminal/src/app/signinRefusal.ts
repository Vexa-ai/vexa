/** What a refused sign-in is told — ONE wording, shared by every door that can say it (Vexa-ai/vexa#1783).
 *
 *  Who may sign in is decided server-side (`signinAdmission` in api/auth/adminApi.ts): an existing
 *  user, an admin, or an address on the instance's allow-list. Two of the doors can show a person
 *  why they were turned away — the emailed link's redeem page and the OAuth callback, which lands
 *  back on the sign-in card with `?error=<code>` — and both must say the same thing, or the same
 *  person learns two different stories about one instance. The emailed-link FORM says nothing at
 *  all: it answers "check your email" for every address, so it cannot be used to learn who is
 *  allowed.
 *
 *  NON-REVEALING by construction: the sentence names no list, no domain and no other address. The
 *  person reading it has already proved they hold this address (a link from its mailbox, or the
 *  provider's word for it), so "this address may not sign in" tells them nothing they could not
 *  learn by trying. Kept free of server imports so the client sign-in card can import it. */

/** An address that is not an existing user, not an admin, and not on the allow-list. */
export const SIGNIN_NOT_ALLOWED = "This email address is not allowed to sign in to this Vexa. Ask its administrator to add it.";

/** Admission could not be decided (admin-api unreachable): refused, because the door fails closed. */
export const SIGNIN_UNAVAILABLE = "Sign-in is unavailable right now. Try again in a minute.";

/** The provider did not vouch for the address: Google has not verified it, or the Microsoft account is
 *  outside this instance's tenant or carries no verified-email claim. */
export const SIGNIN_UNVERIFIED =
  "That account's email address isn't verified by its provider, so it can't sign in here. Use the emailed sign-in link instead.";

/** The `?error=` codes the OAuth door redirects to. NextAuth owns `AccessDenied` and friends; these
 *  two are ours, so the card can say the precise sentence instead of a provider-shaped shrug. */
export const SIGNIN_ERROR_NOT_ALLOWED = "SigninNotAllowed";
export const SIGNIN_ERROR_UNAVAILABLE = "SigninUnavailable";
export const SIGNIN_ERROR_UNVERIFIED = "SigninUnverified";

/** The sentence for an `?error=` code on the sign-in card, or null when there is nothing to say. */
export function signinErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  if (code === SIGNIN_ERROR_NOT_ALLOWED) return SIGNIN_NOT_ALLOWED;
  if (code === SIGNIN_ERROR_UNAVAILABLE) return SIGNIN_UNAVAILABLE;
  if (code === SIGNIN_ERROR_UNVERIFIED) return SIGNIN_UNVERIFIED;
  // Any other NextAuth error (a cancelled consent screen, a provider hiccup) — the sign-in did not
  // happen, and the card says so without guessing why.
  return "That sign-in did not complete. Try again.";
}
