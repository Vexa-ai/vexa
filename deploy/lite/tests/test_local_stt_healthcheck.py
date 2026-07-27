"""ALLOY: verify Lite's optional local-Whisper healthcheck command bundle."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _wsl_path(path: Path) -> str:
    resolved = PureWindowsPath(path.resolve())
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{'/'.join(resolved.parts[1:])}"


def _reexec_in_wsl() -> None:
    completed = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu",
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
        ],
        check=False,
        timeout=60,
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


class LocalSttHealthcheckTest(unittest.TestCase):
    def test_exact_one_adds_the_local_whisper_healthcheck_override(self) -> None:
        """A missing healthcheck override would keep the local Whisper defect alive."""
        command = _whisper_run("1")
        self.assertIn("--health-cmd=", command)
        self.assertIn("python3 -c", command)
        self.assertIn("urllib.request.urlopen", command)
        self.assertIn("--health-interval=5s", command)
        self.assertIn("--health-timeout=3s", command)
        self.assertIn("--health-retries=30", command)

    def test_disabled_values_leave_the_local_whisper_command_unchanged(self) -> None:
        """A truthy check would wrongly alter upstream behavior for non-exact flag values."""
        for value in (None, "", "0", "true"):
            with self.subTest(value=value):
                self.assertNotIn("--health-cmd", _whisper_run(value))
                self.assertNotIn("urllib.request.urlopen", _whisper_run(value))


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
