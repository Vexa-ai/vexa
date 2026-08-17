"""Errors, each carrying a stable machine code.

Every refusal in this module names *why* with a code a caller can branch on and a log can be
searched for. Free-text reasons drift; codes do not.
"""

from __future__ import annotations


class ContextStackError(Exception):
    """Base class. ``code`` is stable; ``message`` is for humans."""

    code = "context-stack-error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFound(ContextStackError):
    code = "not-found"


class AccessDenied(ContextStackError):
    """A decision refused the actor. ``decision`` carries the full verdict for the audit trail."""

    code = "access-denied"

    def __init__(self, message: str, *, decision) -> None:  # noqa: ANN001 - avoids a circular import
        super().__init__(message, code=decision.reason)
        self.decision = decision


class InvalidWorkspace(ContextStackError):
    code = "invalid-workspace"


class ProposalAlreadyDecided(ContextStackError):
    """A decided proposal is final. Re-deciding would silently rewrite a human's answer."""

    code = "proposal-already-decided"
