"""The provider adapters, one per kind — the ONE table that says which ``adapter`` names exist.

``models.v1``'s ``Adapter`` enum is held equal to this table by a test, so a kind is added in two
places that a red test ties together: a module here, and its name in the published contract."""
from __future__ import annotations

from control_plane.model_providers.adapters.anthropic import AnthropicAdapter
from control_plane.model_providers.adapters.custom import CustomAdapter
from control_plane.model_providers.adapters.openai_compatible import OpenAICompatibleAdapter
from control_plane.model_providers.adapters.openrouter import OpenRouterAdapter

ADAPTERS = {a.kind: a for a in (OpenAICompatibleAdapter(), OpenRouterAdapter(),
                                AnthropicAdapter(), CustomAdapter())}

__all__ = ["ADAPTERS"]
