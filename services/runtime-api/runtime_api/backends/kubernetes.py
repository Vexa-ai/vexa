"""Kubernetes backend — manages containers as K8s Pods.

Supports resource limits, GPU passthrough, node selectors,
image pull secrets, and /dev/shm memory-backed volumes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from runtime_api import config
from runtime_api.backends import Backend, ContainerInfo, ContainerSpec

logger = logging.getLogger("runtime_api.backends.kubernetes")

MANAGED_LABEL = "runtime.managed"


class KubernetesBackend(Backend):
    def __init__(self):
        self._api = None
        self._watch_task: Optional[asyncio.Task] = None

    def _get_api(self):
        if self._api is not None:
            return self._api
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            try:
                k8s_config.load_kube_config()
                logger.info("Loaded kubeconfig from file")
            except k8s_config.ConfigException:
                logger.error("Could not load Kubernetes config")
                raise
        self._api = client.CoreV1Api()
        return self._api

    async def startup(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._get_api)

    async def shutdown(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()

    async def create(self, spec: ContainerSpec) -> str:
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        api = self._get_api()
        ns = config.K8S_NAMESPACE

        env_vars = [client.V1EnvVar(name=k, value=v) for k, v in spec.env.items()]

        # Resource requirements
        requests = {}
        limits = {}
        if spec.cpu_request:
            requests["cpu"] = spec.cpu_request
        if spec.memory_request:
            requests["memory"] = spec.memory_request
        if spec.cpu_limit:
            limits["cpu"] = spec.cpu_limit
        if spec.memory_limit:
            limits["memory"] = spec.memory_limit
        resources = client.V1ResourceRequirements(
            requests=requests or None,
            limits=limits or None,
        )

        # Volumes
        volumes = []
        volume_mounts = []

        # /dev/shm as memory-backed volume if shm_size requested
        if spec.shm_size:
            volumes.append(client.V1Volume(
                name="dshm",
                empty_dir=client.V1EmptyDirVolumeSource(medium="Memory"),
            ))
            volume_mounts.append(client.V1VolumeMount(
                name="dshm", mount_path="/dev/shm",
            ))

        container = client.V1Container(
            name="main",
            image=spec.image,
            image_pull_policy=config.K8S_IMAGE_PULL_POLICY,
            command=spec.command or None,
            env=env_vars or None,
            resources=resources,
            volume_mounts=volume_mounts or None,
        )

        # GPU passthrough
        if spec.gpu:
            if not limits:
                limits = {}
            limits["nvidia.com/gpu"] = "1"
            container.resources = client.V1ResourceRequirements(
                requests=requests or None,
                limits=limits,
            )

        # Labels
        labels = {
            **spec.labels,
            MANAGED_LABEL: "true",
        }

        # Image pull secrets
        image_pull_secrets = None
        if config.K8S_IMAGE_PULL_SECRET:
            image_pull_secrets = [
                client.V1LocalObjectReference(name=config.K8S_IMAGE_PULL_SECRET)
            ]

        # K8s-specific overrides (tolerations, affinity, annotations, etc.)
        k8s = spec.k8s_overrides or {}

        pod_spec_kwargs = {
            "restart_policy": "Never",
            "service_account_name": config.K8S_SERVICE_ACCOUNT or None,
            "image_pull_secrets": image_pull_secrets,
            "node_selector": spec.node_selector or None,
            "containers": [container],
            "volumes": volumes or None,
            # Bot shutdown path needs time: MediaRecorder stop + final chunk
            # upload + ffmpeg mux + MinIO upload + callbacks. 60s covers the
            # observed Teams bot shutdown time (Bug B 2026-04-21 — exit 137
            # after 12s active was grace-period-expiry during the graceful
            # leave sequence, not bot-runtime failure). Matches stop() timeout.
            "termination_grace_period_seconds": 60,
        }
        if k8s.get("tolerations"):
            pod_spec_kwargs["tolerations"] = [
                client.V1Toleration(**t) for t in k8s["tolerations"]
            ]
        if k8s.get("affinity"):
            pod_spec_kwargs["affinity"] = k8s["affinity"]

        annotations = k8s.get("annotations", {})

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=spec.name,
                namespace=ns,
                labels=labels,
                annotations=annotations or None,
            ),
            spec=client.V1PodSpec(**pod_spec_kwargs),
        )

        loop = asyncio.get_event_loop()
        try:
            created = await loop.run_in_executor(
                None,
                lambda: api.create_namespaced_pod(namespace=ns, body=pod),
            )
            logger.info(f"Created pod {created.metadata.name} in namespace {ns}")
            # v0.10.5 Pack D.1 (#261) — return pod NAME, not UID.
            #
            # Old shape returned `pod.metadata.uid` (a UUID like
            # `75c21577-29f0-4472-98f1-341ecf7c279b`). meeting-api stored
            # this as `bot_container_id` and later called
            # `DELETE /containers/{bot_container_id}` to stop the bot.
            # runtime-api's stop() looks the pod up by NAME via
            # `delete_namespaced_pod(name=...)`. Looking up the UID
            # against the name field returned 404 → "Pod not found,
            # already deleted" → silent no-op. Bot pod ran forever.
            #
            # Pod name (e.g. `meeting-23-00efafef`) is unique within a
            # namespace and is the canonical identifier kubectl/K8s API
            # use. Returning it as `container_id` makes DELETE work.
            return created.metadata.name
        except ApiException as e:
            if e.status == 409:
                logger.info(f"Pod {spec.name} already exists")
                return spec.name
            logger.error(f"K8s API error creating pod: {e.status} {e.reason}")
            raise

    async def stop(self, name: str, timeout: int = 60) -> bool:
        # Bug B fix (2026-04-21): grace bumped 30→60s. Teams bot exhibited
        # exit 137 during graceful leave sequence (Teams + ffmpeg mux +
        # final-chunk upload exceeded 30s). Matches pod spec's
        # terminationGracePeriodSeconds=60. Pack B incremental upload gives
        # most data durability; 60s handles the slower platforms reliably.
        from kubernetes.client.rest import ApiException

        api = self._get_api()
        ns = config.K8S_NAMESPACE
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: api.delete_namespaced_pod(
                    name=name, namespace=ns, grace_period_seconds=timeout,
                ),
            )
            logger.info(f"Deleted pod {name}")
            return True
        except ApiException as e:
            if e.status == 404:
                logger.info(f"Pod {name} not found, already deleted")
                return True
            logger.error(f"K8s API error deleting pod {name}: {e.status}")
            return False

    async def remove(self, name: str) -> bool:
        # In K8s, stop == remove (deleting a pod removes it)
        return await self.stop(name)

    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        from kubernetes.client.rest import ApiException

        api = self._get_api()
        ns = config.K8S_NAMESPACE
        loop = asyncio.get_event_loop()
        try:
            pod = await loop.run_in_executor(
                None,
                lambda: api.read_namespaced_pod(name=name, namespace=ns),
            )
            return _pod_to_info(pod)
        except ApiException as e:
            if e.status == 404:
                return None
            logger.error(f"K8s API error inspecting pod {name}: {e.status}")
            return None

    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        from kubernetes.client.rest import ApiException

        api = self._get_api()
        ns = config.K8S_NAMESPACE
        loop = asyncio.get_event_loop()

        label_parts = [f"{MANAGED_LABEL}=true"]
        if labels:
            label_parts.extend(f"{k}={v}" for k, v in labels.items())
        label_selector = ",".join(label_parts)

        try:
            pod_list = await loop.run_in_executor(
                None,
                lambda: api.list_namespaced_pod(
                    namespace=ns, label_selector=label_selector,
                ),
            )
        except ApiException as e:
            logger.error(f"K8s API error listing pods: {e.status}")
            return []

        results = []
        for pod in pod_list.items:
            info = _pod_to_info(pod)
            if info:
                results.append(info)
        return results

    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        from kubernetes.stream import stream as k8s_stream

        api = self._get_api()
        ns = config.K8S_NAMESPACE
        loop = asyncio.get_event_loop()

        resp = await loop.run_in_executor(
            None,
            lambda: k8s_stream(
                api.connect_get_namespaced_pod_exec,
                name, ns,
                command=cmd,
                stderr=True, stdin=False, stdout=True, tty=False,
                _preload_content=False,
            ),
        )
        try:
            while resp.is_open():
                data = await loop.run_in_executor(None, resp.read_stdout)
                if data:
                    yield data.encode() if isinstance(data, str) else data
                else:
                    break
        finally:
            resp.close()

    async def listen_events(self, on_exit: callable) -> None:
        self._watch_task = asyncio.create_task(self._watch_pods(on_exit))

    async def _watch_pods(self, on_exit: callable) -> None:
        """Watch for pod phase changes to detect exits."""
        from kubernetes import watch
        from kubernetes.client.rest import ApiException

        loop = asyncio.get_event_loop()
        ns = config.K8S_NAMESPACE

        while True:
            try:
                w = watch.Watch()
                api = self._get_api()
                stream = w.stream(
                    api.list_namespaced_pod,
                    namespace=ns,
                    label_selector=f"{MANAGED_LABEL}=true",
                    timeout_seconds=0,
                )

                def _iterate():
                    for event in stream:
                        etype = event.get("type")
                        pod = event.get("object")
                        if not pod or not pod.status:
                            continue
                        phase = pod.status.phase
                        if etype in ("MODIFIED", "DELETED") and phase in ("Succeeded", "Failed"):
                            name = pod.metadata.name
                            exit_code = 0
                            if pod.status.container_statuses:
                                cs = pod.status.container_statuses[0]
                                if cs.state and cs.state.terminated:
                                    exit_code = cs.state.terminated.exit_code or 0
                            asyncio.run_coroutine_threadsafe(
                                on_exit(name, exit_code), loop,
                            )

                await loop.run_in_executor(None, _iterate)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("K8s watch reconnecting...", exc_info=True)
                await asyncio.sleep(5)


def _pod_to_info(pod) -> Optional[ContainerInfo]:
    if not pod or not pod.metadata:
        return None
    phase = pod.status.phase if pod.status else "Unknown"
    status_map = {
        "Running": "running",
        "Pending": "pending",
        "Succeeded": "exited",
        "Failed": "failed",
        "Unknown": "unknown",
    }
    exit_code = None
    if pod.status and pod.status.container_statuses:
        cs = pod.status.container_statuses[0]
        if cs.state and cs.state.terminated:
            exit_code = cs.state.terminated.exit_code

    created_at = None
    if pod.metadata.creation_timestamp:
        created_at = pod.metadata.creation_timestamp.timestamp()

    pod_ip = pod.status.pod_ip if pod.status else None

    return ContainerInfo(
        # v0.10.5 Pack D.1 (#261) — `id` is the pod NAME, not UID. UID is
        # K8s-internal and not the field DELETE uses. Pod name is the
        # canonical lookup key for the K8s API. Same fix as the create
        # path above (return created.metadata.name).
        id=pod.metadata.name,
        name=pod.metadata.name,
        status=status_map.get(phase, "unknown"),
        exit_code=exit_code,
        labels=pod.metadata.labels or {},
        created_at=created_at,
        image=pod.spec.containers[0].image if pod.spec and pod.spec.containers else None,
        ip=pod_ip,
    )
