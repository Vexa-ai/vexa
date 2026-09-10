"""In-process ports consumed by the service-authority application logic."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from .models import ServiceAuthorityDecision, ServiceAuthorityRequest


@runtime_checkable
class ServiceAuthority(Protocol):
    configured: bool
    mode: str

    async def decide(
        self,
        request: ServiceAuthorityRequest,
    ) -> ServiceAuthorityDecision:
        ...


class ServiceAuthorityDenied(Exception):
    """A configured authority returned an enforced deny."""

    def __init__(
        self,
        reason: str,
        decision_id: str,
        message: Optional[str] = None,
        action_url: Optional[str] = None,
    ) -> None:
        self.reason = reason
        self.decision_id = decision_id
        # Carried so the HTTP edge can hand the caller the deciding service's own words. Default
        # None keeps every existing raiser (and every test that constructs this by hand) valid.
        self.message = message
        self.action_url = action_url
        super().__init__(reason)

    def caller_fields(self) -> dict:
        """The present caller-facing fields, absent ones omitted."""
        fields = {}
        if self.message is not None:
            fields["message"] = self.message
        if self.action_url is not None:
            fields["action_url"] = self.action_url
        return fields


class ServiceAuthorityUnavailable(Exception):
    """A configured authority could not return a trustworthy decision."""
