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
from fastapi import HTTPException
from app.orchestrators.common import enforce_user_concurrency_limit, count_user_active_bots

logger = logging.getLogger("bot_manager.nomad_utils")

# Nomad connection parameters - MODIFIED to use injected Nomad agent IP and fail if not present
NOMAD_AGENT_IP = os.getenv("NOMAD_IP_http")
if not NOMAD_AGENT_IP:
    raise RuntimeError(
        "NOMAD_IP_http environment variable not set. "
        "This is required for the bot-manager to connect to the Nomad API."
    )
NOMAD_ADDR = os.getenv("NOMAD_ADDR", f"http://{NOMAD_AGENT_IP}:4646").rstrip("/")

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
    # Concurrency limit is now checked in request_bot (fast-fail). Keep minimal here.

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
    except httpx.HTTPStatusError as e:
        error_details = "Unknown error"
        try:
            error_body = e.response.text
            if error_body:
                error_details = error_body
        except Exception:
            pass
        logger.error(
            "HTTP %s error dispatching Nomad job to %s: %s. Response body: %s",
            e.response.status_code, NOMAD_ADDR, e, error_details
        )
    except httpx.HTTPError as e:
        logger.error("HTTP error talking to Nomad at %s: %s", NOMAD_ADDR, e)
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected error dispatching Nomad job: %s", e)

    return None, None


def stop_bot_container(container_id: str) -> bool:
    """Stop (force-fail) a dispatched Nomad job by ID.

    Uses the Nomad API to stop the job allocation.
    """
    logger.info(f"Stopping Nomad allocation {container_id}")
    
    try:
        # Use requests for synchronous operation
        import requests
        
        # First try to stop as an allocation ID
        url = f"{NOMAD_ADDR}/v1/allocation/{container_id}/stop"
        resp = requests.post(url, timeout=10)
        if resp.status_code == 200:
            logger.info(f"Successfully stopped allocation {container_id}")
            return True
        if resp.status_code == 404:
            logger.warning(f"Allocation {container_id} not found as allocation. Falling back to job deregister.")
        else:
            logger.warning(f"Allocation stop returned HTTP {resp.status_code}. Falling back to job deregister for {container_id}.")

        # Fallback: treat container_id as job ID and deregister with purge
        try:
            job_url = f"{NOMAD_ADDR}/v1/job/{container_id}/deregister?purge=true"
            job_resp = requests.post(job_url, timeout=10)
            if job_resp.status_code in (200, 202, 404):
                logger.info(f"Job deregister fallback for {container_id} returned HTTP {job_resp.status_code}.")
                return True
            logger.error(f"Job deregister fallback failed for {container_id}: HTTP {job_resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"Error during job deregister fallback for {container_id}: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error stopping allocation {container_id}: {e}")
    return False


async def get_running_bots_status(user_id: int) -> List[Dict[str, Any]]:
    """Return a list of running bots for the given user by querying Nomad API.
    
    Queries the Nomad API to find all running vexa-bot jobs and filters them
    by the user_id in the job metadata.
    """
    logger.info(f"Querying Nomad for running bots for user {user_id}")
    
    try:
        # Query Nomad for all running vexa-bot jobs
        url = f"{NOMAD_ADDR}/v1/jobs"
        params = {"prefix": BOT_JOB_NAME}
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=10)
            resp.raise_for_status()
            jobs_data = resp.json()
            
            running_bots = []
            
            for job in jobs_data:
                # Only process vexa-bot jobs
                if not job.get("ID", "").startswith(BOT_JOB_NAME):
                    continue
                    
                # Check if job is active or pending
                job_status = job.get("Status", "")
                if job_status not in ["running", "pending", "dead", "complete"]:
                    continue
                
                # Get job details to access metadata
                job_id = job.get("ID")
                job_detail_url = f"{NOMAD_ADDR}/v1/job/{job_id}"
                
                try:
                    detail_resp = await client.get(job_detail_url, timeout=10)
                    detail_resp.raise_for_status()
                    job_detail = detail_resp.json()
                    
                    # Extract metadata from the job
                    job_meta = job_detail.get("Meta", {})
                    job_user_id = job_meta.get("user_id")
                    
                    # Only include bots for the requested user
                    if job_user_id and str(job_user_id) == str(user_id):
                        # Get allocation info for container details
                        allocations_url = f"{NOMAD_ADDR}/v1/job/{job_id}/allocations"
                        alloc_resp = await client.get(allocations_url, timeout=10)
                        alloc_resp.raise_for_status()
                        allocations = alloc_resp.json()
                        
                        container_id = None
                        if allocations:
                            # Use the first allocation ID as container ID
                            container_id = allocations[0].get("ID")
                        
                        # Map normalized status for clients
                        normalized = None
                        if job_status == "running":
                            normalized = "Up"
                        elif job_status == "pending":
                            normalized = "Starting"
                        elif job_status in ["dead", "complete"]:
                            normalized = "Exited"

                        bot_status = {
                            "container_id": container_id,
                            "container_name": job_id,
                            "platform": job_meta.get("platform"),
                            "native_meeting_id": job_meta.get("native_meeting_id"),
                            "status": job_status,
                            "normalized_status": normalized,
                            "created_at": job.get("SubmitTime"),
                            "labels": job_meta,
                            "meeting_id_from_name": job_meta.get("meeting_id")
                        }
                        
                        running_bots.append(bot_status)
                        logger.debug(f"Found running bot: {bot_status}")
                        
                except Exception as detail_error:
                    logger.warning(f"Failed to get details for job {job_id}: {detail_error}")
                    continue
            
            logger.info(f"Found {len(running_bots)} running bots for user {user_id}")
            return running_bots
            
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} error querying Nomad jobs: {e}")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error talking to Nomad at {NOMAD_ADDR}: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error querying Nomad for running bots: {e}")
    
    # Return empty list on any error
    return []


async def verify_container_running(container_id: str) -> bool:
    """Return True if the dispatched Nomad job is still running.

    Queries the Nomad API to check if the job allocation is still active.
    """
    logger.debug(f"Verifying if Nomad allocation {container_id} is still running")
    
    try:
        # Query Nomad for the specific allocation
        url = f"{NOMAD_ADDR}/v1/allocation/{container_id}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            allocation_data = resp.json()
            
            # Check if allocation is running
            client_status = allocation_data.get("ClientStatus", "")
            is_running = client_status in ["running", "pending"]
            
            logger.debug(f"Allocation {container_id} client status: {client_status}, running: {is_running}")
            return is_running
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.debug(f"Allocation {container_id} not found (404), not running")
            return False
        logger.warning(f"HTTP {e.response.status_code} error checking allocation {container_id}: {e}")
        return False
    except httpx.HTTPError as e:
        logger.warning(f"HTTP error checking allocation {container_id}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error checking allocation {container_id}: {e}")
        return False

# Alias for shared function – import lazily to avoid circulars
from app.orchestrator_utils import _record_session_start  # noqa: E402 