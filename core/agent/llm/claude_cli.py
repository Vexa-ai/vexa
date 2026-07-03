"""claude_cli.py — CompletionPort adapter that rides the Claude Code CLI (vendor adapter file).

For SUBSCRIPTION-credential deployments: the mounted ``~/.claude/.credentials.json`` OAuth login
works only through the ``claude`` CLI, not the raw HTTP APIs — so this adapter lets the meeting
card beats (plain completions) run on the same credentials as the harness, with no API key.
Select with ``VEXA_LLM_PROVIDER=claude-cli``.

One ``claude -p <prompt> --output-format json`` per completion, TOOL-LESS (``--allowedTools ""``
is verified deny-all) and run from a NEUTRAL cwd — a beat must never load workspace project
memory; steering lives exclusively in the prompt. Slower than a raw HTTP completion (CLI startup
per call); prefer ``openai-compat``/``anthropic`` when an API-style credential exists.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Optional

from llm.errors import LLMAuthError, LLMError, looks_like_auth_failure
from llm.ports import CompletionResult

# A blocking CLI runner: argv, cwd, timeout → (returncode, stdout+stderr text). Injected for tests.
CliRun = Callable[[list[str], str, float], tuple[int, str]]


def _run_subprocess(argv: list[str], cwd: str, timeout: float) -> tuple[int, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def build_argv(prompt: str, *, system: Optional[str] = None, model: Optional[str] = None) -> list[str]:
    """Tool-less headless completion argv. ``--output-format json`` → one terminal JSON object
    (``result`` + ``is_error``); no ``--model`` when empty ⇒ the subscription default."""
    argv = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", ""]
    if system:
        argv += ["--append-system-prompt", system]
    if model:
        argv += ["--model", model]
    return argv


class ClaudeCliCompletion:
    name = "claude-cli"

    def __init__(self, *, model: Optional[str] = None, timeout: float = 180.0,
                 run_fn: Optional[CliRun] = None, cwd: Optional[str] = None) -> None:
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        self._timeout = timeout
        self._run: CliRun = run_fn or _run_subprocess
        # Neutral cwd: never the workspace, so the CLI can't auto-load project memory into a beat.
        self._cwd = cwd or "/tmp"

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 model: Optional[str] = None) -> CompletionResult:
        target = (model or "").strip() or self._model  # empty ⇒ subscription default (no --model)
        argv = build_argv(prompt, system=system, model=target or None)
        try:
            code, out = self._run(argv, self._cwd, self._timeout)
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude-cli completion timed out after {self._timeout:.0f}s") from exc
        except OSError as exc:  # CLI missing from the image
            raise LLMError(f"claude-cli completion failed to spawn: {exc}") from exc

        reply, is_error = "", False
        try:
            # --output-format json prints ONE JSON object; tolerate stray log lines around it.
            # No object at all ⇒ the CLI never produced a terminal result ⇒ an error, not "".
            start, end = out.find("{"), out.rfind("}")
            if start < 0 or end < start:
                raise ValueError("no terminal JSON object in CLI output")
            data = json.loads(out[start:end + 1])
            reply = str(data.get("result") or "")
            is_error = bool(data.get("is_error")) or data.get("subtype") == "error"
        except (ValueError, TypeError):
            is_error = True

        if code != 0 or is_error:
            detail = (reply or out).strip()[:600]
            if looks_like_auth_failure(detail):
                raise LLMAuthError(f"claude-cli auth failure: {detail}")
            raise LLMError(f"claude-cli completion failed (exit {code}): {detail}")
        return CompletionResult(text=reply, model=target or "subscription-default")
