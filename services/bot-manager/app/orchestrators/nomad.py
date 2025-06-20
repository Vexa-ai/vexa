"""Nomad orchestrator implementation (Option A ― Pluggable Launcher).

This module provides minimal stubs that allow the bot-manager to run with
ORCHESTRATOR=nomad.  Only start_bot_container is implemented; the other
functions currently raise NotImplementedError and can be completed later.
"""
from __future__ import annotations

import os
import uuid
import logging
import json
from typing import Optional, Tuple, Dict, Any, List

import httpx

logger = logging.getLogger("bot_manager.nomad_utils")

# Nomad connection parameters
NOMAD_ADDR = os.getenv("NOMAD_ADDR", "http://localhost:4646").rstrip("/")
# Name of the *parameterised* job that represents a vexa-bot instance
BOT_JOB_NAME = os.getenv("VEXA_BOT_JOB_NAME", "vexa-bot")

# ---------------------------------------------------------------------------
# Helper / compatibility no-ops ------------------------------------------------

def get_socket_session(*_args, **_kwargs):  # type: ignore
    """Return None – kept for API compatibility (Docker-specific concept)."""
    return None

def close_client():  # type: ignore
    """No persistent Nomad client yet – nothing to close."""
    return None

close_docker_client = close_client  # compatibility alias

# ---------------------------------------------------------------------------
# Core public API -------------------------------------------------------------

async def start_bot_container(
    user_id: int,
    meeting_id: int,
    meeting_url: Optional[str],
    platform: str,
    bot_name: Optional[str],
    user_token: str,
    native_meeting_id: str,
    language: Optional[str],
    task: Optional[str]
) -> Optional[Tuple[str, str]]:
    """Dispatch a parameterised *vexa-bot* Nomad job.

    Returns (dispatched_job_id, connection_id) on success.
    """
    connection_id = str(uuid.uuid4())

    meta: Dict[str, str] = {
        "user_id": str(user_id),
        "meeting_id": str(meeting_id),
        "meeting_url": meeting_url or "",
        "platform": platform,
        "bot_name": bot_name or "",
        "user_token": user_token or "",
        "native_meeting_id": native_meeting_id,
        "connection_id": connection_id,
        "language": language or "",
        "task": task or "",
    }

    # Nomad job dispatch endpoint
    url = f"{NOMAD_ADDR}/v1/job/{BOT_JOB_NAME}/dispatch"

    # According to Nomad docs, metadata can be supplied in JSON body.
    payload = {
        "Meta": meta
    }

    logger.info(
        f"Dispatching Nomad job '{BOT_JOB_NAME}' for meeting {meeting_id} with meta {meta} -> {url}"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            dispatched_id = data.get("DispatchedJobID") or data.get("EvaluationID")
            if not dispatched_id:
                logger.warning(
                    "Nomad dispatch response missing DispatchedJobID; full response: %s", data
                )
                dispatched_id = f"unknown-{uuid.uuid4()}"
            logger.info(
                "Successfully dispatched Nomad job. Dispatch ID=%s, connection_id=%s",
                dispatched_id,
                connection_id,
            )
            return dispatched_id, connection_id
    except httpx.HTTPError as e:
        logger.error("HTTP error talking to Nomad at %s: %s", NOMAD_ADDR, e)
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected error dispatching Nomad job: %s", e)

    return None, None


def stop_bot_container(container_id: str) -> bool:  # type: ignore
    """Stop (force-fail) a dispatched Nomad job by ID.

    For now this is a stub that logs the request and returns False to indicate
    the operation is not implemented.
    """
    logger.warning(
        "stop_bot_container called for %s but Nomad stop not yet implemented.",
        container_id,
    )
    return False


async def get_running_bots_status(user_id: int) -> List[Dict[str, Any]]:  # type: ignore
    """Return a list of running bots for the given user.

    Stub implementation – returns an empty list.
    """
    logger.info(
        "get_running_bots_status called for user %s – not yet implemented for Nomad.",
        user_id,
    )
    return []


async def verify_container_running(container_id: str) -> bool:  # type: ignore
    """Return True if the dispatched Nomad job is still running.

    Stub implementation – always returns True.
    """
    logger.debug(
        "verify_container_running called for %s – assuming running (stub).",
        container_id,
    )
    return True

# Alias for shared function – import lazily to avoid circulars
from app.docker_utils import _record_session_start  # noqa: E402 