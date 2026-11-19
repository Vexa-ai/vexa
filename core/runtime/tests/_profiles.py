"""The shipped profiles' runnables with a test image — so a backend test exercises the profile data
the deployment runs (labels, network setting, forward list, credential mounts), not a hand copy."""
from __future__ import annotations

from dataclasses import replace

from runtime_kernel import default_registry
from runtime_kernel.profiles import Runnable


def agent(image: str = "img") -> Runnable:
    return replace(default_registry().get("agent").runnable, image=image)


def bot(image: str = "bot:1") -> Runnable:
    return replace(default_registry().get("meeting-bot").runnable, image=image)
