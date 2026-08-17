"""Deliver — where a rendered artifact goes.

**The v0 choice, stated.** The artifact email is not built here. It is handed to the
chat-door **postman** through its command line, because the postman is where the magic-link
signing key lives and re-implementing the link would fork the signing scheme — two
implementations of one capability, drifting, with a bearer credential in the middle. Going
through the process boundary also keeps the service boundary intact: the pipeline does not
import ``chat_door`` (a service importing another service's internals is a ``gate:isolation``
violation), it invokes a published entry point.

The cost is real and named: the coupling is a CLI contract (``--artifact PATH --to EMAIL``)
and an argument list in configuration, so a change to the postman's flags breaks this at run
time rather than at import time. :class:`CommandDelivery` is therefore generic — the postman
is one configuration of it — and the contract is asserted in a test that runs against a real
chat-door checkout when one is pointed at it.

Three sinks ship:

* :class:`FileDelivery` — writes the artifact and its JSON sidecar to a directory. The dev
  sink, and the shape the review corpus is in. Addresses nobody.
* :class:`CommandDelivery` — hands the artifact to an external command. The postman path.
* :class:`NullDelivery` — renders and records, delivers nothing. Used to look at what a run
  *would* send before it sends it.

Every sink must be safe to call twice: the pipeline guards duplicates from the run log, but
a crash between the send and the record is possible and the second run must not double-mail.
``FileDelivery`` overwrites in place; ``CommandDelivery`` inherits whatever idempotency the
command has, which for the postman is a fresh ``Message-ID`` per call — so the pipeline's
ledger, not the sink, is what actually prevents a duplicate email.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from .artifact import Artifact, Recipient
from .ports import DeliveryResult


class NullDelivery:
    """Deliver nothing, and say so. The default when no sink is configured."""

    name = "null"
    requires_address = False

    def deliver(self, artifact: Artifact, recipient: Recipient) -> DeliveryResult:
        return DeliveryResult(status="not_delivered", detail="no delivery sink configured")


class FileDelivery:
    """Write ``<root>/<meeting_id>/<slug>.md`` plus a ``.json`` sidecar carrying the schema.

    The sidecar exists because the markdown is for a person and the JSON is for the next
    program: the postman today infers the artifact's language from whether the first 400
    characters contain Cyrillic, and the schema states it outright.
    """

    name = "file"
    requires_address = False

    def __init__(self, root: Path | str, *, write_sidecar: bool = True) -> None:
        self.root = Path(root)
        self._sidecar = write_sidecar

    def deliver(self, artifact: Artifact, recipient: Recipient) -> DeliveryResult:
        directory = self.root / str(artifact.meeting_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{recipient.slug}.md"
        path.write_text(artifact.to_markdown(), "utf-8")
        if self._sidecar:
            path.with_suffix(".json").write_text(
                json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
        return DeliveryResult(status=DeliveryResult.SENT, detail="file", reference=str(path))


class CommandDelivery:
    """Hand the artifact to an external command.

    ``argv`` is a template list whose entries may contain ``{artifact}`` (a temp file holding
    the canonical markdown), ``{to}``, ``{meeting_id}`` and ``{slug}``. Nothing is passed
    through a shell.
    """

    requires_address = True

    def __init__(
        self,
        argv: Sequence[str],
        *,
        name: str = "command",
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self._argv = list(argv)
        self._cwd = str(cwd) if cwd else None
        self._env = dict(env) if env else None
        self._timeout = timeout

    def deliver(self, artifact: Artifact, recipient: Recipient) -> DeliveryResult:
        if not recipient.email:
            return DeliveryResult(
                status=DeliveryResult.NO_ADDRESS,
                detail=f"{recipient.display_name} has no address in the directory",
            )
        with tempfile.TemporaryDirectory(prefix="vexa-artifact-") as tmp:
            path = Path(tmp) / f"{recipient.slug}.md"
            path.write_text(artifact.to_markdown(), "utf-8")
            argv = [
                a.format(
                    artifact=str(path),
                    to=recipient.email,
                    meeting_id=artifact.meeting_id,
                    slug=recipient.slug,
                )
                for a in self._argv
            ]
            try:
                done = subprocess.run(
                    argv,
                    cwd=self._cwd,
                    env=self._env,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return DeliveryResult(
                    status=DeliveryResult.FAILED, detail=f"{type(exc).__name__}: {exc}"
                )
        if done.returncode != 0:
            return DeliveryResult(
                status=DeliveryResult.FAILED,
                detail=_tail(done.stderr or done.stdout, done.returncode),
            )
        return DeliveryResult(
            status=DeliveryResult.SENT, detail=self.name, reference=_tail(done.stdout, 0)
        )


def postman_delivery(
    chat_door_dir: Path | str,
    *,
    base_url: str,
    smtp_host: str = "127.0.0.1",
    smtp_port: int = 1025,
    from_addr: str | None = None,
    python: str = "python",
    scope: str = "guest",
) -> CommandDelivery:
    """The chat-door postman, configured as a :class:`CommandDelivery`.

    ``chat_door_dir`` is the postman's package directory
    (``core/meetings/services/chat-door``); the door and the postman must share
    ``CHAT_DOOR_SIGNING_KEY`` in the environment or the links it mints verify against
    nothing. The key is never read, printed or logged here — it is inherited from the
    process environment and belongs to the postman.
    """
    argv = [
        python,
        "-m",
        "chat_door.postman",
        "--artifact",
        "{artifact}",
        "--to",
        "{to}",
        "--base-url",
        base_url,
        "--smtp-host",
        smtp_host,
        "--smtp-port",
        str(smtp_port),
        "--scope",
        scope,
    ]
    if from_addr:
        argv += ["--from-addr", from_addr]
    env = dict(os.environ)
    env["PYTHONPATH"] = "src" + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return CommandDelivery(argv, name="postman", cwd=chat_door_dir, env=env)


def _tail(text: str, code: int) -> str:
    body = (text or "").strip().splitlines()
    last = body[-1] if body else ""
    return f"exit {code}: {last}" if code else last


__all__ = ["CommandDelivery", "FileDelivery", "NullDelivery", "postman_delivery"]
