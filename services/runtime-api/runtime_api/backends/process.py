"""Process backend — manages containers as child processes.

Designed for single-host deployments where Docker is unavailable.
Uses Redis-backed registry (not in-memory) with a periodic reaper loop
that checks every 30s for dead processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import resource
import signal
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from runtime_api import config
from runtime_api.backends import Backend, ContainerInfo, ContainerSpec
from runtime_api.utils import parse_memory

logger = logging.getLogger("runtime_api.backends.process")

# Redis key prefix for process registry
PROCESS_PREFIX = "runtime:process:"
MANAGED_LABEL = "runtime.managed"


class ProcessBackend(Backend):
    def __init__(self, redis=None):
        self._redis = redis
        self._reaper_task: Optional[asyncio.Task] = None
        self._event_callback = None

    def set_redis(self, redis):
        self._redis = redis

    async def startup(self) -> None:
        logs_path = Path(config.PROCESS_LOGS_DIR)
        try:
            logs_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create logs directory: {e}")

    async def shutdown(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()

    async def create(self, spec: ContainerSpec) -> str:
        if not spec.command:
            raise ValueError("Process backend requires spec.command")

        # Check working directory
        working_dir = spec.working_dir
        if working_dir and not Path(working_dir).exists():
            raise FileNotFoundError(f"Working directory not found: {working_dir}")

        # Prepare environment
        env = os.environ.copy()
        env.update(spec.env)

        # If config data is too large for env, write to temp file
        for key, value in spec.env.items():
            if len(value) > 32000:
                tmp_path = Path(config.PROCESS_LOGS_DIR) / f"{spec.name}.{key.lower()}.json"
                tmp_path.write_text(value)
                env[f"{key}_FILE"] = str(tmp_path)
                env[key] = str(tmp_path)  # keep for compat but also set _FILE
                logger.info(f"Wrote large env var {key} ({len(value)} bytes) to {tmp_path}")

        # Log file
        log_file = Path(config.PROCESS_LOGS_DIR) / f"{spec.name}.log"

        # Resource limits from spec
        def _set_limits():
            os.setsid()  # new process group for clean termination
            if spec.memory_limit:
                mem_bytes = parse_memory(spec.memory_limit)
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        try:
            log_handle = open(log_file, "w")
            proc = subprocess.Popen(
                spec.command,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=working_dir,
                preexec_fn=_set_limits,
            )
        except Exception as e:
            logger.error(f"Failed to start process {spec.name}: {e}", exc_info=True)
            raise

        pid = str(proc.pid)
        logger.info(f"Started process {spec.name} (PID={pid})")

        # Store in Redis registry
        process_data = {
            "pid": proc.pid,
            "name": spec.name,
            "command": spec.command,
            "image": spec.image,
            "labels": spec.labels,
            "env_keys": list(spec.env.keys()),
            "created_at": time.time(),
            "status": "running",
            "log_file": str(log_file),
            "working_dir": working_dir,
        }
        if self._redis:
            await self._redis.set(
                f"{PROCESS_PREFIX}{spec.name}",
                json.dumps(process_data),
            )

        return pid

    async def stop(self, name: str, timeout: int = 10) -> bool:
        proc_data = await self._get_process_data(name)
        if not proc_data:
            logger.info(f"Process {name} not in registry")
            return True

        pid = proc_data.get("pid")
        if not pid:
            return True

        success = await asyncio.to_thread(_terminate_process_group, pid, timeout)

        # Update registry
        if self._redis:
            proc_data["status"] = "stopped"
            proc_data["stopped_at"] = time.time()
            await self._redis.set(
                f"{PROCESS_PREFIX}{name}",
                json.dumps(proc_data),
                ex=86400,  # 24h TTL
            )

        return success

    async def remove(self, name: str) -> bool:
        if self._redis:
            await self._redis.delete(f"{PROCESS_PREFIX}{name}")
        return True

    async def inspect(self, name: str) -> Optional[ContainerInfo]:
        proc_data = await self._get_process_data(name)
        if not proc_data:
            return None

        pid = proc_data.get("pid")
        status = proc_data.get("status", "unknown")

        # Check if actually alive
        if status == "running" and pid:
            if not _pid_alive(pid):
                status = "exited"
                proc_data["status"] = "exited"
                if self._redis:
                    await self._redis.set(
                        f"{PROCESS_PREFIX}{name}",
                        json.dumps(proc_data),
                        ex=86400,
                    )

        return ContainerInfo(
            id=str(pid) if pid else name,
            name=name,
            status=status,
            labels=proc_data.get("labels", {}),
            created_at=proc_data.get("created_at"),
            image=proc_data.get("image", ""),
        )

    async def list(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        if not self._redis:
            return []

        results = []
        async for key in self._redis.scan_iter(f"{PROCESS_PREFIX}*"):
            raw = await self._redis.get(key)
            if not raw:
                continue
            data = json.loads(raw)

            # Filter by labels
            if labels:
                proc_labels = data.get("labels", {})
                if not all(proc_labels.get(k) == v for k, v in labels.items()):
                    continue

            name = data.get("name", key.removeprefix(PROCESS_PREFIX))
            pid = data.get("pid")
            status = data.get("status", "unknown")

            results.append(ContainerInfo(
                id=str(pid) if pid else name,
                name=name,
                status=status,
                labels=data.get("labels", {}),
                created_at=data.get("created_at"),
                image=data.get("image", ""),
            ))
        return results

    async def exec(self, name: str, cmd: list[str]) -> AsyncIterator[bytes]:
        proc_data = await self._get_process_data(name)
        if not proc_data:
            return

        working_dir = proc_data.get("working_dir")
        env = os.environ.copy()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=working_dir,
            env=env,
        )
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
        await proc.wait()

    async def listen_events(self, on_exit: callable) -> None:
        self._event_callback = on_exit
        self._reaper_task = asyncio.create_task(self._reaper_loop(on_exit))

    async def _reaper_loop(self, on_exit: callable) -> None:
        """Check every PROCESS_REAPER_INTERVAL seconds for dead processes."""
        while True:
            try:
                await asyncio.sleep(config.PROCESS_REAPER_INTERVAL)
                await self._reap_dead(on_exit)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Reaper loop error", exc_info=True)

    async def _reap_dead(self, on_exit: callable) -> None:
        if not self._redis:
            return

        async for key in self._redis.scan_iter(f"{PROCESS_PREFIX}*"):
            raw = await self._redis.get(key)
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("status") != "running":
                continue

            pid = data.get("pid")
            if not pid or not _pid_alive(pid):
                name = data.get("name", key.removeprefix(PROCESS_PREFIX))
                logger.info(f"Reaper: process {name} (PID={pid}) is dead")

                # Get exit code if possible
                exit_code = 0
                if pid:
                    try:
                        _, status = os.waitpid(pid, os.WNOHANG)
                        if os.WIFEXITED(status):
                            exit_code = os.WEXITSTATUS(status)
                    except ChildProcessError:
                        pass

                data["status"] = "exited"
                data["stopped_at"] = time.time()
                data["exit_code"] = exit_code
                await self._redis.set(key, json.dumps(data), ex=86400)

                if on_exit:
                    await on_exit(name, exit_code)

    async def _get_process_data(self, name: str) -> Optional[dict]:
        if not self._redis:
            return None
        raw = await self._redis.get(f"{PROCESS_PREFIX}{name}")
        if raw:
            return json.loads(raw)
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def _terminate_process_group(pid: int, timeout: int = 10) -> bool:
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                logger.info(f"Process {pid} terminated gracefully")
                return True

        logger.warning(f"Process {pid} did not terminate, sending SIGKILL")
        os.killpg(pgid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except PermissionError:
        logger.error(f"Permission denied terminating process {pid}")
        return False
    except Exception as e:
        logger.error(f"Error terminating process {pid}: {e}")
        return False


