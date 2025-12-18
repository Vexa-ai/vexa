"""Orchestrator module loader.

This module dynamically loads the appropriate orchestrator implementation
based on the ORCHESTRATOR environment variable.

Supported orchestrators:
- docker (default): Uses Docker socket to spawn bot containers
- nomad: Uses HashiCorp Nomad to dispatch parameterized jobs
- process: Spawns bots as local Node.js processes (for Lite deployments)
"""
import os
import importlib
import logging

logger = logging.getLogger("bot_manager.orchestrator_loader")

_orchestrator = os.getenv("ORCHESTRATOR", "docker").lower()

# Select the appropriate orchestrator module based on ORCHESTRATOR env var
if _orchestrator == "nomad":
    module_name = "app.orchestrators.nomad"
elif _orchestrator == "process":
    module_name = "app.orchestrators.process"
else:
    # Default to Docker orchestrator
    module_name = "app.orchestrators.docker"

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