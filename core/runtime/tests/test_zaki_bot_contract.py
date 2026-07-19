"""ZAKI's packaged meeting-bot Pod contract is validated and materialized offline."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from runtime_kernel.bot_contract import BOT_CONTRACT_ENV, BotPodContract, BotPodContractError
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable, default_registry

SOURCE_SHA = "a" * 40
DIGEST = "b" * 64
BOT_IMAGE = f"ghcr.io/projectnuggets/zaki-minutes-bot:sha-{SOURCE_SHA}@sha256:{DIGEST}"


def _contract_document() -> dict:
    return {
        "contractVersion": 1,
        "image": BOT_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "imagePullSecret": "ghcr-projectnuggets",
        "serviceAccountName": "zaki-minutes-bot",
        "automountServiceAccountToken": False,
        "labels": {"app.kubernetes.io/name": "zaki-minutes-bot"},
        "terminationGracePeriodSeconds": 120,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "fsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containerSecurityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "500m", "memory": "1Gi", "ephemeral-storage": "2Gi"},
            "limits": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "10Gi"},
        },
        "volumes": [
            {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
            {"name": "dshm", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        ],
        "volumeMounts": [
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "dshm", "mountPath": "/dev/shm"},
        ],
    }


def _captured_pod(monkeypatch, runnable: Runnable, env: dict[str, str]) -> tuple[tuple[str, ...], dict]:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(*args: str, **_kwargs):
        calls.append(args)

    monkeypatch.setattr("runtime_kernel.k8s_backend._kubectl", fake_kubectl)
    K8sBackend(name_prefix="zaki-", namespace="minutes").start("rt-123", runnable, env)
    assert len(calls) == 1
    args = calls[0]
    return args, json.loads(args[args.index("--overrides") + 1])


def test_registry_uses_the_contract_image_without_browser_image(monkeypatch):
    monkeypatch.delenv("BROWSER_IMAGE", raising=False)
    monkeypatch.setenv(BOT_CONTRACT_ENV, json.dumps(_contract_document()))

    bot = default_registry().resolve("meeting-bot")

    assert bot is not None
    assert bot.image == BOT_IMAGE
    # The contract image owns `/app/entrypoint.sh`; Kubernetes `command` replaces image
    # ENTRYPOINT, so retaining the vendored `/app/vexa-bot/…` command would crash the bot.
    assert bot.command == ["/app/entrypoint.sh"]
    assert bot.bot_pod_contract is not None
    assert bot.bot_pod_contract.service_account_name == "zaki-minutes-bot"


def test_registry_fails_closed_for_a_broadened_supplied_contract(monkeypatch):
    document = _contract_document()
    document["containerSecurityContext"]["readOnlyRootFilesystem"] = False
    monkeypatch.setenv("BROWSER_IMAGE", "registry.example.test/fallback:1")
    monkeypatch.setenv(BOT_CONTRACT_ENV, json.dumps(document))

    with pytest.raises(BotPodContractError, match="readOnlyRootFilesystem"):
        default_registry()


def test_contract_rejects_a_public_or_mutable_bot_image():
    document = _contract_document()
    document["image"] = "vexaai/vexa-bot:v012"

    with pytest.raises(BotPodContractError, match="immutable ghcr"):
        BotPodContract.from_document(document)


def test_k8s_start_materializes_restricted_contract_as_a_complete_container(monkeypatch):
    document = _contract_document()
    contract = BotPodContract.from_document(copy.deepcopy(document))
    env = {
        "VEXA_BOT_CONFIG": "{\"platform\":\"google_meet\"}",
    }
    args, override = _captured_pod(
        monkeypatch,
        Runnable(
            image=BOT_IMAGE,
            command=["/app/vexa-bot/entrypoint.sh"],
            bot_pod_contract=contract,
        ),
        env,
    )

    assert args[:2] == ("run", "zaki-rt-123")
    assert "-n" in args and "minutes" in args
    assert not any(argument.startswith("--env") for argument in args)
    assert "--command" not in args

    spec = override["spec"]
    container = spec["containers"][0]
    assert container["name"] == "zaki-rt-123"
    assert container["image"] == BOT_IMAGE
    assert container["command"] == ["/app/vexa-bot/entrypoint.sh"]
    assert {entry["name"]: entry["value"] for entry in container["env"]} == env
    assert container["imagePullPolicy"] == "IfNotPresent"
    assert container["securityContext"] == document["containerSecurityContext"]
    assert container["resources"] == document["resources"]
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {"/tmp", "/dev/shm"}
    assert {volume["name"] for volume in spec["volumes"]} == {"tmp", "dshm"}
    assert spec["restartPolicy"] == "Never"
    assert spec["serviceAccountName"] == "zaki-minutes-bot"
    assert spec["automountServiceAccountToken"] is False
    assert spec["terminationGracePeriodSeconds"] == 120
    assert spec["securityContext"] == document["securityContext"]
    assert spec["imagePullSecrets"] == [{"name": "ghcr-projectnuggets"}]
    assert override["metadata"]["labels"] == {
        "app.kubernetes.io/name": "zaki-minutes-bot",
        "runtime.managed": "true",
        "runtime.workload_id": "rt-123",
    }


def test_contracted_bot_rejects_workload_controlled_workspace_mounts(monkeypatch):
    contract = BotPodContract.from_document(_contract_document())
    with pytest.raises(ValueError, match="cannot mount a workspace"):
        _captured_pod(
            monkeypatch,
            Runnable(
                image=BOT_IMAGE,
                command=["/app/entrypoint.sh"],
                bot_pod_contract=contract,
            ),
            {
                "VEXA_BOT_CONFIG": "{}",
                "VEXA_WORKSPACE_MOUNT_SOURCE": "agent-workspaces",
                "VEXA_WORKSPACE_MOUNT_TARGET": "/workspaces",
                "VEXA_WORKSPACE_PATH": "/workspaces/tenant-a",
            },
        )


def test_k8s_start_without_contract_still_emits_a_complete_generated_container(monkeypatch):
    env = {"VEXA_X": "y"}
    args, override = _captured_pod(
        monkeypatch,
        Runnable(image="registry.example.test/worker:1", command=["sleep", "30"]),
        env,
    )

    container = override["spec"]["containers"][0]
    assert container == {
        "name": "zaki-rt-123",
        "image": "registry.example.test/worker:1",
        "command": ["sleep", "30"],
        "env": [{"name": "VEXA_X", "value": "y"}],
    }
    assert "securityContext" not in override["spec"]
    assert not any(argument.startswith("--env") for argument in args)
    assert "--command" not in args


def test_bot_image_declares_the_non_root_runtime_user():
    dockerfile = Path(__file__).resolve().parents[3] / "core/meetings/services/bot/Dockerfile"
    entrypoint = Path(__file__).resolve().parents[3] / "core/meetings/services/bot/entrypoint.sh"

    assert "USER 10001:10001" in dockerfile.read_text()
    assert "XDG_RUNTIME_DIR" in entrypoint.read_text()
