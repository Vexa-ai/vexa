"""GENERATED from core/identity/contracts/signin.v1/signin.schema.json by gen.mjs — DO NOT EDIT.
Regenerate with: node core/identity/contracts/signin.v1/gen.mjs

The reason vocabularies of the sign-in admission wire (signin.v1), shared with the terminal's
clients/terminal/src/app/api/auth/signinWire.ts, which is generated from the same schema.
"""
from __future__ import annotations

from typing import Literal, Tuple

ADMITTED_REASONS: Tuple[str, ...] = ("admin", "admin-email", "existing-user", "allow-list", "claim-code")
AdmittedReason = Literal["admin", "admin-email", "existing-user", "allow-list", "claim-code"]

REFUSED_REASONS: Tuple[str, ...] = ("not-allowed",)
RefusedReason = Literal["not-allowed"]

CLAIM_REASONS: Tuple[str, ...] = ("claimed", "admin-exists", "bad-code", "not-allowed")
ClaimReason = Literal["claimed", "admin-exists", "bad-code", "not-allowed"]

SigninReason = Literal["admin", "admin-email", "existing-user", "allow-list", "claim-code", "not-allowed"]

CLAIM_CODE_MAX_LENGTH = 64
EMAIL_MAX_LENGTH = 320
