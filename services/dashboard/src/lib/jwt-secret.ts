/**
 * Secret for signing and verifying magic-link tokens.
 *
 * There is no fallback: when `JWT_SECRET` is unset (or still a placeholder),
 * magic-link sign-in is refused rather than signed with a guessable key.
 */
const PLACEHOLDER_SECRETS = new Set(["default-secret-change-me", "change-me", "changeme"]);

export const JWT_SECRET_NOT_CONFIGURED = {
  error: "Email sign-in is not configured on this dashboard (JWT_SECRET is not set).",
  code: "JWT_SECRET_NOT_CONFIGURED",
} as const;

export function getJwtSecret(): string | null {
  const secret = process.env.JWT_SECRET?.trim();
  if (!secret || PLACEHOLDER_SECRETS.has(secret.toLowerCase())) return null;
  return secret;
}
