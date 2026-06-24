"""RunPod backend — manages bot pods via RunPod REST API.

Spawns GPU pods per meeting, polls for exit, cleans up.
Uses Redis-backed registry (same pattern as process backend).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from runtime_api import config
from runtime_api.backends import Backend, ContainerInfo, ContainerSpec

logger = logging.getLogger("runtime_api.backends.runpod")

RUNPOD_PREFIX = "runtime:runpod:"
MANAGED_LABEL = "runtime.managed"
RUNPOD_API_BASE = "https://rest.runpod.io/v1"

_STATUS_MAP = {
    "RUNNING": "running",
    "EXITED": "exited",
    "TERMINATED": "exited",
}

_CAPACITY_ERROR_MARKERS = (
    "there are no instances currently available",
    "insufficient capacity",
)


class RunPodBackend(Backend):
    def __init__(self, redis=None):
        self._redis = redis
        self._client: Optional[httpx.AsyncClient] = None
        self._reaper_task: Optional[asyncio.Task] = None

    def set_redis(self, redis):
        self._redis = redis

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=RUNPOD_API_BASE,
                headers={"Authorization": f"Bearer {config.RUNPOD_API_KEY}"},
                timeout=30.0,
            )
        return self._client

    async def startup(self) -> None:
        if not config.RUNPOD_API_KEY:
            raise ValueError("RUNPOD_API_KEY is required for runpod backend")
        self._client = httpx.AsyncClient(
            base_url=RUNPOD_API_BASE,
            headers={"Authorization": f"Bearer {config.RUNPOD_API_KEY}"},
            timeout=30.0,
        )
        try:
            resp = await self._client.get("/pods", params={"computeType": "GPU"})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 404):
                logger.warning(
                    "RunPod API preflight skipped: pod listing returned HTTP %s",
                    exc.response.status_code,
                )
                return
            raise

        logger.info(f"RunPod API connected ({len(resp.json())} GPU pods)")

    async def shutdown(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def create(self, spec: ContainerSpec) -> str:
        client = self._get_client()

        env = dict(spec.env)
        env["RUNPOD_POD_NAME"] = spec.name

        base_payload: dict = {
            "name": spec.name,
            "imageName": spec.image,
            "containerDiskInGb": config.RUNPOD_CONTAINER_DISK_GB,
            "env": env,
            "ports": ["22/tcp"],
            "supportPublicIp": True,
        }

        if spec.gpu:
            gpu_types = list(config.RUNPOD_GPU_TYPES) or [config.RUNPOD_GPU_TYPE]
            last_capacity_error = ""
            attempted_gpu_types: list[str] = []

            for gpu_type in gpu_types:
                attempted_gpu_types.append(gpu_type)
                payload = {
                    **base_payload,
                    "computeType": "GPU",
                    "gpuCount": 1,
                    "gpuTypeIds": [gpu_type],
                    "cloudType": config.RUNPOD_CLOUD_TYPE,
                    "volumeInGb": 0,
                }

                resp = await client.post("/pods", json=payload)
                if resp.is_success:
                    pod = resp.json()
                    pod_id = pod["id"]
                    logger.info(f"Created RunPod pod {spec.name} ({pod_id}) using GPU {gpu_type}")
                    return await self._record_created_pod(spec, pod_id, gpu_type)

                error_text = self._extract_error_text(resp)
                if self._is_capacity_error(error_text):
                    last_capacity_error = error_text
                    logger.warning(
                        "RunPod GPU %s unavailable for %s: %s",
                        gpu_type,
                        spec.name,
                        error_text,
                    )
                    continue

                resp.raise_for_status()

            attempted = ", ".join(attempted_gpu_types)
            detail = last_capacity_error or "RunPod returned no usable capacity error details"
            raise RuntimeError(
                f"RunPod GPU capacity unavailable for {spec.name}. Tried: {attempted}. "
                f"Last error: {detail}"
            )
        else:
            payload = {
                **base_payload,
                "computeType": "CPU",
                "vcpuCount": 4,
            }

            resp = await client.post("/pods", json=payload)
            resp.raise_for_status()
            pod = resp.json()
            pod_id = pod["id"]

            logger.info(f"Created RunPod pod {spec.name} ({pod_id})")
            return await self._record_created_pod(spec, pod_id, None)

    async def _record_created_pod(
        self,
        spec: ContainerSpec,
        pod_id: str,
        gpu_type: str | None,
    ) -> str:
        pod_data = {
            "pod_id": pod_id,
            "name": spec.name,
            "image": spec.image,
            "labels": {**spec.labels, MANAGED_LABEL: "true"},
            "env_keys": list(spec.env.keys()),
            "created_at": time.time(),
            "status": "pending",
        }
        if gpu_type:
            pod_data["gpu_type"] = gpu_type
        if self._redis:
            await self._redis.set(
                f"{RUNPOD_PREFIX}{spec.name}",
                json.dumps(pod_data),
            )

        return pod_id

    @staticmethod
    def _extract_error_text(resp: httpx.Response) -> str:
        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            errors = payload.get("errors")
            if isinstance(errors, list):
                parts = [str(item).strip() for item in errors if str(item).strip()]
                if parts:
                    return "; ".join(parts)

        text = resp.text.strip()
        if text:
            return text
        return f"HTTP {resp.status_code}"

    @staticmethod
    def _is_capacity_error(error_text: str) -> bool:
        text = error_text.lower()
        return any(marker in text for marker in _CAPACITY_ERROR_MARKERS)

    async def stop(self, name: str, timeout: int = 10) -> bool:
        data = await self._get_pod_data(name)
        if not data:
            return True

        pod_id = data.get("pod_id")
        if not pod_id:
            return True

        client = self._get_client()
        try:
            resp = await client.post(f"/pods/{pod_id}/stop")
            if resp.status_code == 404:
                return True
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Stop pod {name} failed: {e}")
            return False

        if self._redis:
            data["status"] = "stopped"
            data["stopped_at"] = time.time()
            await self._redis.set(f"{RUNPOD_PREFIX}{name}", json.dumps(data), ex=86400)

        return True

    async def remove(self, name: str) -> bool:
        data = await self._get_pod_data(name)
        if not data:
            return True

        pod_id = data.get("pod_id")
        if pod_id:
            client = self._get_client()
            try:
                resp = await client.delete(f"/pods/{pod_id}")
                if resp.status_code not in (204, 404):
                    logger.warning(f"Delete pod {name}: HTTP {resp.status_code}")
            except Exception as e:
                logger.warning(f"Delete pod {name} failed: {e}")

        if self._redis:
            await self._redis.delete(f"{RUNPOD_PREFIX}{name}")
        return True

    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        data = await self._get_pod_data(name)
        if not data:
            return None

        pod_id = data.get("pod_id")
        if not pod_id:
            return None

        client = self._get_client()
        try:
            resp = await client.get(f"/pods/{pod_id}")
            if resp.status_code == 404:
                data["status"] = "exited"
                if self._redis:
                    await self._redis.set(f"{RUNPOD_PREFIX}{name}", json.dumps(data), ex=86400)
                return ContainerInfo(
                    id=pod_id, name=name, status="exited",
                    labels=data.get("labels", {}),
                    created_at=data.get("created_at"),
                    image=data.get("image", ""),
                )
            resp.raise_for_status()
            pod = resp.json()
        except Exception as e:
            logger.warning(f"Inspect pod {name} failed: {e}")
            return ContainerInfo(
                id=pod_id, name=name, status="unknown",
                labels=data.get("labels", {}),
                created_at=data.get("created_at"),
                image=data.get("image", ""),
            )

        desired = pod.get("desiredStatus", "UNKNOWN")
        status = _STATUS_MAP.get(desired, "pending")
        public_ip = pod.get("publicIp")
        port_mappings = pod.get("portMappings") or {}

        if status != data.get("status"):
            data["status"] = status
            if self._redis:
                await self._redis.set(f"{RUNPOD_PREFIX}{name}", json.dumps(data))

        return ContainerInfo(
            id=pod_id, name=name, status=status,
            labels=data.get("labels", {}),
            created_at=data.get("created_at"),
            image=pod.get("image", data.get("image", "")),
            ip=public_ip,
            ports={str(k): v for k, v in port_mappings.items()},
        )

    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        if not self._redis:
            return []

        results = []
        async for key in self._redis.scan_iter(f"{RUNPOD_PREFIX}*"):
            raw = await self._redis.get(key)
            if not raw:
                continue
            data = json.loads(raw)

            if labels:
                pod_labels = data.get("labels", {})
                if not all(pod_labels.get(k) == v for k, v in labels.items()):
                    continue

            name = data.get("name", key.removeprefix(RUNPOD_PREFIX))
            results.append(ContainerInfo(
                id=data.get("pod_id", name),
                name=name,
                status=data.get("status", "unknown"),
                labels=data.get("labels", {}),
                created_at=data.get("created_at"),
                image=data.get("image", ""),
            ))
        return results

    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        logger.warning(f"exec not supported for RunPod backend (pod {name})")
        return
        yield

    async def listen_events(self, on_exit: callable) -> None:
        self._reaper_task = asyncio.create_task(self._reaper_loop(on_exit))

    async def _reaper_loop(self, on_exit: callable) -> None:
        while True:
            try:
                await asyncio.sleep(config.RUNPOD_POLL_INTERVAL)
                await self._reap_dead(on_exit)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Reaper loop error", exc_info=True)

    async def _reap_dead(self, on_exit: callable) -> None:
        if not self._redis:
            return

        async for key in self._redis.scan_iter(f"{RUNPOD_PREFIX}*"):
            raw = await self._redis.get(key)
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("status") not in ("running", "pending"):
                continue

            pod_id = data.get("pod_id")
            if not pod_id:
                continue

            name = data.get("name", key.removeprefix(RUNPOD_PREFIX))
            client = self._get_client()

            try:
                resp = await client.get(f"/pods/{pod_id}")
                if resp.status_code == 404:
                    exit_code = 0
                    data["status"] = "exited"
                    data["stopped_at"] = time.time()
                    data["exit_code"] = exit_code
                    await self._redis.set(key, json.dumps(data), ex=86400)
                    if on_exit:
                        await on_exit(name, exit_code)
                    continue

                resp.raise_for_status()
                pod = resp.json()
                desired = pod.get("desiredStatus", "UNKNOWN")

                if desired in ("EXITED", "TERMINATED"):
                    exit_code = 0
                    data["status"] = "exited"
                    data["stopped_at"] = time.time()
                    data["exit_code"] = exit_code
                    await self._redis.set(key, json.dumps(data), ex=86400)
                    logger.info(f"Reaper: pod {name} ({pod_id}) {desired.lower()}")
                    if on_exit:
                        await on_exit(name, exit_code)
            except Exception:
                logger.debug(f"Failed to check pod {name}", exc_info=True)

    async def _get_pod_data(self, name: str) -> Optional[dict]:
        if not self._redis:
            return None
        raw = await self._redis.get(f"{RUNPOD_PREFIX}{name}")
        if raw:
            return json.loads(raw)
        return None
