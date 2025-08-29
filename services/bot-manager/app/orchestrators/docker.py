"""Docker orchestrator shim.
Re-exports a stable set of symbols from ``app.orchestrator_utils`` so that the
rest of the codebase can import them from ``app.orchestrators`` regardless of
the chosen backend.
"""

# NOTE: We import *explicit* names, including the leading-underscore coroutine,
# because ``from module import *`` skips private names by default.  Omitting it
# caused ``_record_session_start`` to be ``None`` when the caller attempted to
# create an asyncio task.

from app.orchestrator_utils import (  # noqa: F401
    get_socket_session,
    close_docker_client,
    start_bot_container,
    stop_bot_container,
    _record_session_start,
    get_running_bots_status,
    verify_container_running,
)

__all__ = [
    "get_socket_session",
    "close_docker_client",
    "start_bot_container",
    "stop_bot_container",
    "_record_session_start",
    "get_running_bots_status",
    "verify_container_running",
] 