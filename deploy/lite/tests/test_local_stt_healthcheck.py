"""ALLOY: verify Lite's optional local-Whisper healthcheck command bundle."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HEALTHCHECK_ARGS = [
    (
        "--health-cmd=python3 -c 'import urllib.request; "
        'urllib.request.urlopen("http://localhost:8000/health", timeout=2).read()\''
    ),
    "--health-interval=5s",
    "--health-timeout=3s",
    "--health-retries=30",
]


def _wsl_path(path: Path) -> str:
    resolved = PureWindowsPath(path.resolve())
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{'/'.join(resolved.parts[1:])}"


def _reexec_in_wsl() -> None:
    command = ["wsl.exe"]
    if distro := os.environ.get("ALLOY_LITE_TEST_WSL_DISTRO", "").strip():
        command.extend(["-d", distro])
    command.extend(
        [
            "--cd",
            _wsl_path(ROOT),
            "--",
            "env",
            "-i",
            f"PATH={POSIX_PATH}",
            "HOME=/tmp",
            "LC_ALL=C",
            "python3",
            "deploy/lite/tests/test_local_stt_healthcheck.py",
            "--posix",
        ]
    )
    completed = subprocess.run(
        command,
        check=False,
        timeout=45,
    )
    raise SystemExit(completed.returncode)


def _whisper_run(flag: str | None) -> str:
    """Return the generated local-Whisper `docker run` command without running Docker."""
    with tempfile.TemporaryDirectory(prefix="vexa-alloy-healthcheck-") as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text("", encoding="utf-8")
        env = {"PATH": POSIX_PATH, "HOME": "/tmp", "LC_ALL": "C"}
        if flag is not None:
            env["ALLOY_STT_HEALTHCHECK"] = flag
        output = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "-f",
                str(MAKEFILE),
                f"ROOT={ROOT}",
                f"ENV_FILE={env_file}",
                "LOCAL_STT=1",
                "up",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
        ).stdout
    start = output.find("docker run -d --name vexa-lite-whisper")
    if start < 0:
        raise AssertionError(f"local Whisper run command missing from make dry-run:\n{output}")
    end = output.find(" > /dev/null;", start)
    if end < 0:
        raise AssertionError(f"local Whisper run command is not complete:\n{output[start:]}")
    return output[start:end]


def _whisper_argv(flag: str | None) -> list[str]:
    """Parse the generated command with POSIX shell quoting rules."""
    return shlex.split(_whisper_run(flag).replace("\\\n", " "), posix=True)


class LocalSttHealthcheckTest(unittest.TestCase):
    def test_exact_one_adds_the_local_whisper_healthcheck_override(self) -> None:
        """A missing healthcheck override would keep the local Whisper defect alive."""
        baseline = _whisper_argv(None)
        enabled = _whisper_argv("1")
        restart = baseline.index("--restart")
        self.assertEqual(baseline[restart : restart + 2], ["--restart", "unless-stopped"])
        insertion = restart + 2

        self.assertEqual(
            enabled,
            [*baseline[:insertion], *HEALTHCHECK_ARGS, *baseline[insertion:]],
        )
        self.assertEqual(enabled.count(HEALTHCHECK_ARGS[0]), 1)
        for timing in HEALTHCHECK_ARGS[1:]:
            self.assertEqual(enabled.count(timing), 1)
        self.assertEqual(enabled[-1], baseline[-1])

    def test_disabled_values_leave_the_local_whisper_command_unchanged(self) -> None:
        """A truthy check would wrongly alter upstream behavior for non-exact flag values."""
        rendered = {
            value: _whisper_argv(value)
            for value in (None, "", "0", "true")
        }
        baseline = rendered[None]
        for value, argv in rendered.items():
            with self.subTest(value=value):
                self.assertEqual(argv, baseline)


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
