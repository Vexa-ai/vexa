"""Kubernetes orchestrator implementation.

Spawns bots as Kubernetes Pods instead of Docker containers or child processes.
Designed for Kubernetes-native deployments where bot-manager runs as a Pod
and creates sibling bot Pods via the Kubernetes API.

Activate with ORCHESTRATOR=kubernetes environment variable.

Requires:
- kubernetes Python client (pip install kubernetes)
- ServiceAccount with pod create/delete/get/list/watch permissions
  (created by the Helm chart's bot-manager-rbac.yaml)
"""
from __future__ import annotations

import os
import uuid
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.orchestrators.common import enforce_user_concurrency_limit, count_user_active_bots

logger = logging.getLogger("bot_manager.kubernetes_orchestrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BOT_IMAGE_NAME = os.getenv("BOT_IMAGE_NAME", "vexa-bot:latest")
BOT_NAMESPACE = os.getenv("BOT_NAMESPACE", os.getenv("POD_NAMESPACE", "default"))
BOT_SERVICE_ACCOUNT = os.getenv("BOT_SERVICE_ACCOUNT_NAME", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
BOT_CALLBACK_BASE_URL = os.getenv("BOT_CALLBACK_BASE_URL", "http://bot-manager:8080")

# Resource limits for bot pods (set via Helm values)
BOT_CPU_REQUEST = os.getenv("BOT_POD_CPU_REQUEST", "500m")
BOT_MEMORY_REQUEST = os.getenv("BOT_POD_MEMORY_REQUEST", "512Mi")
BOT_CPU_LIMIT = os.getenv("BOT_POD_CPU_LIMIT", "2000m")
BOT_MEMORY_LIMIT = os.getenv("BOT_POD_MEMORY_LIMIT", "4Gi")

# Image pull policy and secrets
IMAGE_PULL_POLICY = os.getenv("IMAGE_PULL_POLICY", "IfNotPresent")
IMAGE_PULL_SECRET = os.getenv("IMAGE_PULL_SECRET", "")

# Node selector for bot pods (JSON string, e.g. '{"role": "bots"}')
BOT_NODE_SELECTOR = json.loads(os.getenv("BOT_NODE_SELECTOR", "{}")) if os.getenv("BOT_NODE_SELECTOR") else {}

# ---------------------------------------------------------------------------
# Kubernetes Client
# ---------------------------------------------------------------------------

_k8s_api: Optional[client.CoreV1Api] = None


def _get_k8s_api() -> client.CoreV1Api:
    """Get or initialize the Kubernetes API client."""
    global _k8s_api
    if _k8s_api is None:
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded kubeconfig from file")
            except config.ConfigException:
                logger.error("Could not load Kubernetes config (in-cluster or kubeconfig)")
                raise
        _k8s_api = client.CoreV1Api()
    return _k8s_api


# ---------------------------------------------------------------------------
# Compatibility Stubs
# ---------------------------------------------------------------------------

def get_socket_session(*_args, **_kwargs):
    """Compatibility stub - no Docker socket in K8s orchestrator."""
    return None


def close_client():
    """Compatibility stub."""
    pass


close_docker_client = close_client


# ---------------------------------------------------------------------------
# Core Public API
# ---------------------------------------------------------------------------

async def start_bot_container(
    user_id: int,
    meeting_id: int,
    meeting_url: Optional[str],
    platform: str,
    bot_name: Optional[str],
    user_token: str,
    native_meeting_id: str,
    language: Optional[str],
    task: Optional[str],
    transcription_tier: Optional[str] = "realtime",
    recording_enabled: Optional[bool] = None,
    transcribe_enabled: Optional[bool] = None,
    zoom_obf_token: Optional[str] = None,
    voice_agent_enabled: Optional[bool] = None,
    default_avatar_url: Optional[str] = None,
    agent_enabled: Optional[bool] = None,
) -> Optional[Tuple[str, str]]:
    """Start a bot as a Kubernetes Pod.

    Returns:
        Tuple of (pod_name, connection_id) on success, (None, None) on failure.
    """
    connection_id = str(uuid.uuid4())
    pod_name = f"vexa-bot-{meeting_id}-{uuid.uuid4().hex[:8]}"

    logger.info(
        f"Starting bot pod {pod_name} for meeting {meeting_id} "
        f"(platform={platform}, connection_id={connection_id})"
    )

    # Mint MeetingToken
    from app.main import mint_meeting_token
    try:
        meeting_token = mint_meeting_token(
            meeting_id=meeting_id,
            user_id=user_id,
            platform=platform,
            native_meeting_id=native_meeting_id,
            ttl_seconds=7200,
        )
    except Exception as e:
        logger.error(f"Failed to mint MeetingToken: {e}", exc_info=True)
        return None, None

    # Load user recording config from DB (same as Docker orchestrator)
    user_recording_config = {}
    try:
        from app.database import async_session_local
        from app.models import User
        async with async_session_local() as db:
            user = await db.get(User, user_id)
            if user and user.data and isinstance(user.data, dict):
                user_recording_config = user.data.get("recording_config", {})
    except Exception as e:
        logger.warning(f"Failed to load user recording config for user {user_id}: {e}")

    # Build BOT_CONFIG JSON
    bot_config = {
        "meeting_id": meeting_id,
        "platform": platform,
        "meetingUrl": meeting_url,
        "botName": bot_name or f"VexaBot-{uuid.uuid4().hex[:6]}",
        "token": meeting_token,
        "nativeMeetingId": native_meeting_id,
        "connectionId": connection_id,
        "language": language,
        "task": task or "transcribe",
        "transcribeEnabled": True if transcribe_enabled is None else bool(transcribe_enabled),
        "transcriptionTier": transcription_tier or "realtime",
        "recordingEnabled": user_recording_config.get("enabled", os.getenv("RECORDING_ENABLED", "true").lower() == "true"),
        "captureModes": user_recording_config.get("capture_modes", os.getenv("CAPTURE_MODES", "audio").split(",")),
        "obfToken": zoom_obf_token if platform == "zoom" else None,
        "redisUrl": REDIS_URL,
        "container_name": pod_name,
        "automaticLeave": {
            "waitingRoomTimeout": 300000,
            "noOneJoinedTimeout": 120000,
            "everyoneLeftTimeout": 60000,
        },
        "botManagerCallbackUrl": f"{BOT_CALLBACK_BASE_URL}/bots/internal/callback/exited",
        "recordingUploadUrl": f"{BOT_CALLBACK_BASE_URL}/internal/recordings/upload",
    }
    if recording_enabled is not None:
        bot_config["recordingEnabled"] = bool(recording_enabled)
    if voice_agent_enabled is not None:
        bot_config["voiceAgentEnabled"] = bool(voice_agent_enabled)
    if default_avatar_url:
        bot_config["defaultAvatarUrl"] = default_avatar_url

    # Transcription service URL — required for per-speaker pipeline
    tx_url = os.getenv("TRANSCRIPTION_SERVICE_URL")
    tx_token = os.getenv("TRANSCRIPTION_SERVICE_TOKEN")
    if tx_url:
        bot_config["transcriptionServiceUrl"] = tx_url
    if tx_token:
        bot_config["transcriptionServiceToken"] = tx_token

    bot_config = {k: v for k, v in bot_config.items() if v is not None}

    # Build environment variables for the bot container
    env_vars = [
        client.V1EnvVar(name="BOT_CONFIG", value=json.dumps(bot_config)),
        client.V1EnvVar(name="LOG_LEVEL", value=os.getenv("LOG_LEVEL", "INFO")),
        client.V1EnvVar(name="DISPLAY", value=":99"),
    ]

    # Zoom credentials
    if platform == "zoom":
        zoom_client_id = os.getenv("ZOOM_CLIENT_ID")
        zoom_client_secret = os.getenv("ZOOM_CLIENT_SECRET")
        if zoom_client_id and zoom_client_secret:
            env_vars.extend([
                client.V1EnvVar(name="ZOOM_CLIENT_ID", value=zoom_client_id),
                client.V1EnvVar(name="ZOOM_CLIENT_SECRET", value=zoom_client_secret),
            ])

    # TTS for voice agent
    if voice_agent_enabled:
        tts_url = os.getenv("TTS_SERVICE_URL", "")
        if tts_url:
            env_vars.append(client.V1EnvVar(name="TTS_SERVICE_URL", value=tts_url))

    # Build the Pod spec
    container = client.V1Container(
        name="vexa-bot",
        image=BOT_IMAGE_NAME,
        image_pull_policy=IMAGE_PULL_POLICY,
        env=env_vars,
        resources=client.V1ResourceRequirements(
            requests={"cpu": BOT_CPU_REQUEST, "memory": BOT_MEMORY_REQUEST},
            limits={"cpu": BOT_CPU_LIMIT, "memory": BOT_MEMORY_LIMIT},
        ),
        volume_mounts=[
            client.V1VolumeMount(name="dshm", mount_path="/dev/shm"),
        ],
    )

    # /dev/shm as memory-backed volume (Chromium needs this)
    shm_volume = client.V1Volume(
        name="dshm",
        empty_dir=client.V1EmptyDirVolumeSource(medium="Memory"),
    )

    image_pull_secrets = None
    if IMAGE_PULL_SECRET:
        image_pull_secrets = [client.V1LocalObjectReference(name=IMAGE_PULL_SECRET)]

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=BOT_NAMESPACE,
            labels={
                "app.kubernetes.io/name": "vexa-bot",
                "app.kubernetes.io/managed-by": "vexa-bot-manager",
                "vexa.user-id": str(user_id),
                "vexa.meeting-id": str(meeting_id),
                "vexa.platform": platform,
            },
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            service_account_name=BOT_SERVICE_ACCOUNT or None,
            image_pull_secrets=image_pull_secrets,
            node_selector=BOT_NODE_SELECTOR or None,
            containers=[container],
            volumes=[shm_volume],
        ),
    )

    try:
        api = _get_k8s_api()
        loop = asyncio.get_event_loop()
        created = await loop.run_in_executor(
            None,
            lambda: api.create_namespaced_pod(namespace=BOT_NAMESPACE, body=pod),
        )
        logger.info(f"Created bot pod {created.metadata.name} in namespace {BOT_NAMESPACE}")
        return pod_name, connection_id

    except ApiException as e:
        logger.error(f"K8s API error creating bot pod: {e.status} {e.reason}", exc_info=True)
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error creating bot pod: {e}", exc_info=True)
        return None, None


def stop_bot_container(container_id: str) -> bool:
    """Stop a bot by deleting its Kubernetes Pod.

    Args:
        container_id: The pod name.

    Returns:
        True if pod was deleted or not found, False on error.
    """
    logger.info(f"Stopping bot pod {container_id}")

    try:
        api = _get_k8s_api()
        api.delete_namespaced_pod(
            name=container_id,
            namespace=BOT_NAMESPACE,
            grace_period_seconds=10,
        )
        logger.info(f"Deleted bot pod {container_id}")
        return True

    except ApiException as e:
        if e.status == 404:
            logger.info(f"Pod {container_id} not found, already deleted")
            return True
        logger.error(f"K8s API error deleting pod {container_id}: {e.status} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Error deleting pod {container_id}: {e}", exc_info=True)
        return False


async def get_running_bots_status(user_id: int) -> List[Dict[str, Any]]:
    """Get status of running bot pods for a user.

    Returns:
        List of bot status dicts matching the Docker orchestrator format.
    """
    try:
        api = _get_k8s_api()
        loop = asyncio.get_event_loop()
        pod_list = await loop.run_in_executor(
            None,
            lambda: api.list_namespaced_pod(
                namespace=BOT_NAMESPACE,
                label_selector=f"app.kubernetes.io/name=vexa-bot,vexa.user-id={user_id}",
            ),
        )
    except ApiException as e:
        logger.error(f"K8s API error listing pods for user {user_id}: {e.status}")
        return []
    except Exception as e:
        logger.error(f"Error listing pods for user {user_id}: {e}", exc_info=True)
        return []

    result = []
    for pod in pod_list.items:
        phase = pod.status.phase  # Pending, Running, Succeeded, Failed, Unknown
        if phase in ("Succeeded", "Failed"):
            continue

        labels = pod.metadata.labels or {}
        created_at = pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None

        # Map K8s phase to normalized status
        normalized = "Up" if phase == "Running" else "Starting" if phase == "Pending" else phase

        # Extract meeting_id from label or pod name
        meeting_id_str = labels.get("vexa.meeting-id", "unknown")

        result.append({
            "container_id": pod.metadata.name,
            "container_name": pod.metadata.name,
            "platform": labels.get("vexa.platform"),
            "native_meeting_id": None,
            "status": phase,
            "normalized_status": normalized,
            "created_at": created_at,
            "labels": {f"vexa.user_id": str(user_id)},
            "meeting_id_from_name": meeting_id_str,
        })

    logger.info(f"Found {len(result)} active bot pods for user {user_id}")
    return result


async def verify_container_running(container_id: str) -> bool:
    """Verify if a bot pod is still running.

    Args:
        container_id: The pod name.

    Returns:
        True if pod exists and is Running or Pending.
    """
    try:
        api = _get_k8s_api()
        loop = asyncio.get_event_loop()
        pod = await loop.run_in_executor(
            None,
            lambda: api.read_namespaced_pod(name=container_id, namespace=BOT_NAMESPACE),
        )
        phase = pod.status.phase
        is_running = phase in ("Running", "Pending")
        logger.debug(f"Pod {container_id} phase={phase}, running={is_running}")
        return is_running

    except ApiException as e:
        if e.status == 404:
            logger.debug(f"Pod {container_id} not found")
            return False
        logger.error(f"K8s API error checking pod {container_id}: {e.status}")
        return False
    except Exception as e:
        logger.error(f"Error checking pod {container_id}: {e}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Session Recording (shared with other orchestrators)
# ---------------------------------------------------------------------------

from app.orchestrator_utils import _record_session_start  # noqa: E402


# ---------------------------------------------------------------------------
# Module Exports
# ---------------------------------------------------------------------------

__all__ = [
    "get_socket_session",
    "close_docker_client",
    "start_bot_container",
    "stop_bot_container",
    "_record_session_start",
    "get_running_bots_status",
    "verify_container_running",
]
