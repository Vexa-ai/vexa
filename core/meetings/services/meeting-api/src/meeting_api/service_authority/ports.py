"""In-process ports consumed by the service-authority application logic."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

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

    def __init__(self, reason: str, decision_id: str) -> None:
        self.reason = reason
        self.decision_id = decision_id
        super().__init__(reason)


class ServiceAuthorityUnavailable(Exception):
    """A configured authority could not return a trustworthy decision."""
