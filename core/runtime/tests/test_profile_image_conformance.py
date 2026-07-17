"""A2 (#675) — image↔profile conformance gate (OFFLINE, durable).

A profile's command becomes argv[0] of the spawned workload under the k8s backend
(`kubectl run --command`) and is the sole argv under the process backend. If that path does not exist
in the target image, every spawn StartErrors — which is exactly how v0.12.7 shipped: the meeting-bot
profile carried `/app/vexa-bot/entrypoint.sh`, absent from the bot image whose ENTRYPOINT is
`/app/entrypoint.sh`. docker's Cmd-append hid it (harmless trailing arg); k8s's entrypoint-replace
detonated it.

The live release-validate leg proves the path with `docker run --rm <image> test -x <path>` against the
PUBLISHED bot image (see .github/workflows/release-validate.yml). This offline twin is the cheap,
durable half: it parses the real bot Dockerfile's ENTRYPOINT and asserts the meeting-bot profile
command is CONSISTENT with it — either empty (so every backend execs the image ENTRYPOINT) or exactly
the ENTRYPOINT path. Any future edit that reintroduces a command the image does not declare fails here,
at head, instead of at a customer's helm install.

RED on main: pre-fix the meeting-bot command argv[0] is /app/vexa-bot/entrypoint.sh, which is neither
empty nor equal to the Dockerfile's ENTRYPOINT (/app/entrypoint.sh) → this test fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from runtime_kernel import default_registry
from runtime_kernel.profiles import apply_command_overrides

# core/  (the dir holding both `runtime` and `meetings`) — same anchor test_profiles.py uses.
CORE = Path(__file__).resolve().parents[2]
BOT_DOCKERFILE = CORE / "meetings" / "services" / "bot" / "Dockerfile"


def _entrypoint_paths(dockerfile: Path) -> list[str]:
    """Return the argv of the last ENTRYPOINT declared in `dockerfile` (exec-form JSON or shell-form).

    Durable and dependency-free: we only need argv[0] (the executable path), and ENTRYPOINT in the bot
    image is the exec-form JSON array `["/app/entrypoint.sh"]`."""
    text = dockerfile.read_text()
    argv: list[str] | None = None
    for m in re.finditer(r"^\s*ENTRYPOINT\s+(.+)$", text, flags=re.MULTILINE):
        raw = m.group(1).strip()
        if raw.startswith("["):
            argv = json.loads(raw)
        else:
            argv = raw.split()
    return argv or []


def test_bot_dockerfile_declares_the_expected_entrypoint():
    """Pin the anchor the gate leans on: the real bot image's launcher path."""
    assert _entrypoint_paths(BOT_DOCKERFILE) == ["/app/entrypoint.sh"]


def test_meeting_bot_command_conforms_to_bot_image_entrypoint():
    """The container-backend meeting-bot command must resolve to a path the shipped bot image has.

    Offline rule (the durable half of the conformance gate): the command is empty — so docker omits Cmd
    and k8s omits --command, both booting the image ENTRYPOINT — OR its argv[0] equals the image's
    declared ENTRYPOINT path. Anything else is the #675 defect class."""
    entry = _entrypoint_paths(BOT_DOCKERFILE)
    assert entry, "bot Dockerfile declares no ENTRYPOINT — conformance anchor missing"
    entry_path = entry[0]

    command = default_registry().resolve("meeting-bot").command
    if command:  # non-empty ⇒ it REPLACES the entrypoint, so argv[0] must be an in-image path
        assert command[0] == entry_path, (
            f"meeting-bot profile command {command!r} does not resolve to the bot image ENTRYPOINT "
            f"{entry_path!r}; under k8s this StartErrors (#675). Drop the command or point it at the "
            f"image's real launcher."
        )
    # command is None ⇒ the image ENTRYPOINT is authoritative on every backend; conformant by design.


def test_meeting_bot_command_conforms_under_a_bogus_override(monkeypatch):
    """The gate covers the post-BOT_COMMAND-override value too: an override that points at a path the
    image does not declare is caught here, not at spawn time.

    (Note: a real process-backend/lite deployment sets BOT_COMMAND to an in-container launcher in a
    DIFFERENT image — lite-process — where the live gate's `docker run test -x` is the authority. This
    offline check pins the CONTAINER-backend contract against the bot Dockerfile.)"""
    entry_path = _entrypoint_paths(BOT_DOCKERFILE)[0]
    monkeypatch.setenv("BOT_COMMAND", "/app/vexa-bot/entrypoint.sh")  # the historical bad path
    reg = apply_command_overrides(default_registry())
    bad = reg.resolve("meeting-bot").command
    assert bad == ["/app/vexa-bot/entrypoint.sh"]
    # The conformance rule would REJECT this override against the bot image — proving the gate's reach.
    with pytest.raises(AssertionError):
        assert not bad or bad[0] == entry_path
