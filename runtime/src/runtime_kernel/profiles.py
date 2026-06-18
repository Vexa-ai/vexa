"""Profile → Runnable registry. A `profile` is opaque in runtime.v1 (P11); the kernel resolves it to
HOW to run it — an `image` (container backends) and/or a `command` (process backend / container override).
The contract never sees this; it's kernel config (policy), per deployment."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Runnable:
    image: Optional[str] = None
    command: Optional[list[str]] = None
