import os
import importlib
import logging

logger = logging.getLogger("bot_manager.orchestrator_loader")

_orchestrator = os.getenv("ORCHESTRATOR", "docker").lower()
module_name = "app.orchestrators.nomad" if _orchestrator == "nomad" else "app.orchestrators.docker"
logger.info(f"Using '{_orchestrator}' orchestrator module: {module_name}")

# Dynamically import the concrete module
mod = importlib.import_module(module_name)

# Re-export a stable interface expected by the rest of the codebase
get_socket_session = getattr(mod, "get_socket_session", lambda *args, **kwargs: None)
close_docker_client = getattr(mod, "close_docker_client", getattr(mod, "close_client", lambda: None))
start_bot_container = mod.start_bot_container  # type: ignore
stop_bot_container = getattr(mod, "stop_bot_container", lambda *args, **kwargs: None)
_record_session_start = getattr(mod, "_record_session_start", lambda *args, **kwargs: None)
get_running_bots_status = getattr(mod, "get_running_bots_status", lambda *args, **kwargs: {})
verify_container_running = getattr(mod, "verify_container_running", lambda *args, **kwargs: False) 