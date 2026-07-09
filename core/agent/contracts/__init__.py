"""contracts — schema loaders/validators for the published cross-domain contracts.

The schema JSON subdirs (workspace.v1/, event.v1/, …) live alongside this package;
``loader`` resolves them by walking ``__file__.parents`` to the ``core/`` root.
"""
from contracts.loader import *  # noqa: F401,F403
from contracts.loader import (
    iter_segments,
    validate_entity_frontmatter,
    validate_event,
    validate_invocation,
    validate_routine,
    validate_segment,
    validate_session_end,
    validate_tool,
    validate_transcription,
    validate_unit_invocation,
)

__all__ = [
    "iter_segments",
    "validate_entity_frontmatter",
    "validate_event",
    "validate_invocation",
    "validate_routine",
    "validate_segment",
    "validate_session_end",
    "validate_tool",
    "validate_transcription",
    "validate_unit_invocation",
]
