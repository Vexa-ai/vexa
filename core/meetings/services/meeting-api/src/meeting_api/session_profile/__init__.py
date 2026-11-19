"""session_profile — an authenticated bot's browser-session write-back.

The bots restore the deployment's stored browser session with a READ-ONLY key; their rotated
session comes back through this module's one route, ``PUT /internal/browser-session/{session_uid}``,
which admits only the live authenticated bot's MeetingToken and stores only SESSION_PROFILE files
(the auth-essential subset the session-profile.v1 contract defines, read from a verbatim copy of its
schema, the same file ``@vexa/remote-browser`` reads) with meeting-api's own storage credentials.

Front door (P6): import from here, never a deep module path.

Public surface:
  * ``build_router(meeting_repo, token_secret=..., writer_factory=..., clock=...)`` — the route.
  * ``SESSION_PROFILE`` / ``profile_path_refusal`` / ``is_profile_path`` / ``parse_profile_upload``
    / ``InvalidSessionProfile`` — the profile definition and the body check.
  * ``S3SessionWriter`` / ``SessionWriter`` — the store writer (boto3) and its port.
  * ``WRITEBACK_GRACE_S`` / ``SESSION_WRITEBACK_ROUTE``.
"""
from __future__ import annotations

from .profile import (
    MAX_BODY_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SESSION_PROFILE,
    SESSION_WRITEBACK_ROUTE,
    InvalidSessionProfile,
    is_profile_path,
    parse_profile_upload,
    profile_path_refusal,
)
from .router import WRITEBACK_GRACE_S, build_router
from .writer import S3SessionWriter, SessionWriter

__all__ = [
    "build_router",
    "SESSION_PROFILE",
    "SESSION_WRITEBACK_ROUTE",
    "WRITEBACK_GRACE_S",
    "MAX_BODY_BYTES",
    "MAX_FILE_BYTES",
    "MAX_FILES",
    "MAX_TOTAL_BYTES",
    "InvalidSessionProfile",
    "is_profile_path",
    "parse_profile_upload",
    "profile_path_refusal",
    "S3SessionWriter",
    "SessionWriter",
]
