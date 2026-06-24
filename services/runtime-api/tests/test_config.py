"""Configuration precedence tests."""

import importlib

from runtime_api import config as config_module


def test_runpod_account_api_key_preferred(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "pod-scoped-key")
    monkeypatch.setenv("RUNPOD_ACCOUNT_API_KEY", "account-key")

    config = importlib.reload(config_module)
    assert config.RUNPOD_API_KEY == "account-key"


def test_runpod_api_key_fallback(monkeypatch):
    monkeypatch.delenv("RUNPOD_ACCOUNT_API_KEY", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "fallback-key")

    config = importlib.reload(config_module)
    assert config.RUNPOD_API_KEY == "fallback-key"


def test_runpod_gpu_types_default_list(monkeypatch):
    monkeypatch.delenv("RUNPOD_GPU_TYPES", raising=False)
    monkeypatch.delenv("RUNPOD_GPU_TYPE", raising=False)

    config = importlib.reload(config_module)
    assert config.RUNPOD_GPU_TYPES == [
        "NVIDIA GeForce RTX 3090",
        "NVIDIA GeForce RTX 5090",
        "NVIDIA RTX A5000",
        "NVIDIA RTX A4000",
    ]


def test_runpod_gpu_types_override(monkeypatch):
    monkeypatch.setenv("RUNPOD_GPU_TYPES", "GPU-A, GPU-B ,GPU-C")

    config = importlib.reload(config_module)
    assert config.RUNPOD_GPU_TYPES == ["GPU-A", "GPU-B", "GPU-C"]
