/** GENERATED from core/identity/contracts/signin.v1/signin.schema.json by gen.mjs — DO NOT EDIT.
 *  Regenerate with: node core/identity/contracts/signin.v1/gen.mjs
 *
 *  The reason vocabularies of the sign-in admission wire (signin.v1), shared with admin-api's
 *  core/identity/services/admin-api/src/admin_api/app/signin_wire.py, generated from the same schema. */

export const ADMITTED_REASONS = ["admin", "admin-email", "existing-user", "allow-list", "claim-code"] as const;
export type AdmittedReason = (typeof ADMITTED_REASONS)[number];

export const REFUSED_REASONS = ["not-allowed"] as const;
export type RefusedReason = (typeof REFUSED_REASONS)[number];

export const CLAIM_REASONS = ["claimed", "admin-exists", "bad-code", "not-allowed"] as const;
export type ClaimReason = (typeof CLAIM_REASONS)[number];

const member = <T extends string>(values: readonly T[]) => (x: unknown): x is T =>
  typeof x === "string" && (values as readonly string[]).includes(x);

export const isAdmittedReason = member(ADMITTED_REASONS);
export const isRefusedReason = member(REFUSED_REASONS);
export const isClaimReason = member(CLAIM_REASONS);

export const CLAIM_CODE_MAX_LENGTH = 64;
