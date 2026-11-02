"""Join-failure screenshots — the one picture of why a bot did not get in, kept off the pod.

The join layer writes checkpoint screenshots inside the bot container; nothing uploaded them, so
they died with the pod (1 Nov 2026: the founder asked to see the screen a prod bot was refused on
and nobody could show it). A bot whose join fails now uploads ONE PNG per attempt here:

  * ``build_router(repo, storage, token_secret=...)`` — ``POST /internal/join-evidence/screenshot``
    (internal, not in the public schema). Same edge, credential and session scoping as the
    recording upload; stores ``join-evidence/<meeting_id>/<session_uid>.png`` in the recordings
    object store and answers with that key, which the bot puts on its terminal ``join_evidence``.
  * ``delete_meeting_screenshots(storage, meeting_id=...)`` — what erasing a meeting removes.

Retention: nothing in the application expires these objects (the signal janitor sweeps only
``signal/``, the recording reconciler only reads ``recordings/``), and the prod bucket's rules expire
only incomplete uploads (7 d) and noncurrent versions (90 d). A screenshot therefore lives until its
meeting is erased — at least the 7 days the retry and its review need.
"""
from .router import (
    JOIN_SCREENSHOT_MAX_BYTES,
    JOIN_SCREENSHOT_PREFIX,
    build_router,
    delete_meeting_screenshots,
    screenshot_key,
)

__all__ = [
    "JOIN_SCREENSHOT_MAX_BYTES",
    "JOIN_SCREENSHOT_PREFIX",
    "build_router",
    "delete_meeting_screenshots",
    "screenshot_key",
]
