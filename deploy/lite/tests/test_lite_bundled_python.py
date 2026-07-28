"""ALLOY: verify the exact opt-in Lite bundled-Python build contract."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "deploy" / "lite" / "Dockerfile.lite"
MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
BUILD_FLAG = "ALLOY_LITE_BUNDLED_PYTHON"
STAGE_ARG = "ALLOY_LITE_PYTHON_STAGE"
UPSTREAM_STAGE = "alloy-lite-python-0"
BUNDLED_STAGE = "alloy-lite-python-1"


def _wsl_path(path: Path) -> str:
    resolved = PureWindowsPath(path.resolve())
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{'/'.join(resolved.parts[1:])}"


def _reexec_in_wsl() -> None:
    command = [
        "wsl.exe",
        "-d",
        os.environ.get("ALLOY_LITE_TEST_WSL_DISTRO", "Ubuntu"),
        "--cd",
        _wsl_path(ROOT),
        "--",
        "env",
        "-i",
        f"PATH={POSIX_PATH}",
        "HOME=/tmp",
        "LC_ALL=C",
        "python3",
        "deploy/lite/tests/test_lite_bundled_python.py",
        "--posix",
    ]
    completed = subprocess.run(command, check=False, timeout=60)
    raise SystemExit(completed.returncode)


def _make_dry_run(
    target: str,
    *,
    env_text: str = "",
    ambient_value: str | None = None,
    make_value: str | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="vexa-alloy-python-build-") as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text(env_text, encoding="utf-8")
        env = {"PATH": POSIX_PATH, "HOME": "/tmp", "LC_ALL": "C"}
        if ambient_value is not None:
            env[BUILD_FLAG] = ambient_value
        args = [
            "make",
            "--no-print-directory",
            "-n",
            "-f",
            str(MAKEFILE),
            f"ROOT={ROOT}",
            f"ENV_FILE={env_file}",
        ]
        if make_value is not None:
            args.append(f"{BUILD_FLAG}={make_value}")
        args.append(target)
        return subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
        ).stdout


def _python_build_args(dry_run: str) -> dict[str, str]:
    return dict(
        re.findall(
            rf"--build-arg\s+({BUILD_FLAG}|{STAGE_ARG})=([^\s\\]+)",
            dry_run,
        )
    )


class LiteBundledPythonTest(unittest.TestCase):
    def test_make_build_and_push_enable_bundled_python_only_for_exact_one(self) -> None:
        """A truthy build switch would alter the upstream path for disabled values."""
        cases = [
            ("absent", "", None, None, "0", UPSTREAM_STAGE),
            ("empty env file", f"{BUILD_FLAG}=\n", None, None, "0", UPSTREAM_STAGE),
            ("zero", "", "0", None, "0", UPSTREAM_STAGE),
            ("truthy text", "", "true", None, "0", UPSTREAM_STAGE),
            ("leading zero", "", "01", None, "0", UPSTREAM_STAGE),
            ("other integer", "", "2", None, "0", UPSTREAM_STAGE),
            ("env file exact one", f"{BUILD_FLAG}=1\n", None, None, "1", BUNDLED_STAGE),
            (
                "ambient disabled overrides enabled file",
                f"{BUILD_FLAG}=1\n",
                "false",
                None,
                "0",
                UPSTREAM_STAGE,
            ),
            (
                "make exact one overrides disabled file",
                f"{BUILD_FLAG}=0\n",
                None,
                "1",
                "1",
                BUNDLED_STAGE,
            ),
        ]
        for target in ("build", "push"):
            for name, env_text, ambient, make_value, flag, stage in cases:
                with self.subTest(target=target, name=name):
                    self.assertEqual(
                        _python_build_args(
                            _make_dry_run(
                                target,
                                env_text=env_text,
                                ambient_value=ambient,
                                make_value=make_value,
                            )
                        ),
                        {BUILD_FLAG: flag, STAGE_ARG: stage},
                    )

    def test_dockerfile_keeps_five_venvs_on_one_selected_python_base(self) -> None:
        """An unconditional copy or duplicated venv branch would change flag-off behavior."""
        source = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "FROM python:3.12-slim-bullseye AS alloy-lite-bundled-python",
            source,
        )
        self.assertRegex(
            source,
            rf"(?m)^FROM [^\n]+ AS {UPSTREAM_STAGE}$",
        )
        self.assertRegex(
            source,
            rf"(?m)^FROM [^\n]+ AS {BUNDLED_STAGE}$",
        )
        self.assertIn(
            f"FROM ${{{STAGE_ARG}}} AS lite-admin-venv",
            source,
        )
        self.assertIn("FROM lite-admin-venv AS final", source)
        self.assertEqual(source.count("uv venv --python 3.12 /opt/venvs/"), 5)

        upstream_start = source.index(f" AS {UPSTREAM_STAGE}")
        upstream_end = source.index("\nFROM ", upstream_start)
        upstream_branch = source[upstream_start:upstream_end]
        self.assertNotIn("COPY --from=alloy-lite-bundled-python", upstream_branch)

        bundled_start = source.index(f" AS {BUNDLED_STAGE}")
        bundled_end = source.index("\nFROM ", bundled_start)
        bundled_branch = source[bundled_start:bundled_end]
        self.assertIn("# ALLOY:", bundled_branch)
        self.assertIn(
            "COPY --from=alloy-lite-bundled-python /usr/local /usr/local",
            bundled_branch,
        )
        self.assertIn(f'ARG {BUILD_FLAG}="0"', bundled_branch)
        self.assertIn(f'test "${BUILD_FLAG}" = "1"', bundled_branch)
        self.assertIn("[ALLOY]", bundled_branch)

    def test_makefile_is_the_single_build_argument_owner(self) -> None:
        """A second build-arg site would let local and multi-arch builds drift."""
        source = MAKEFILE.read_text(encoding="utf-8")
        owner_start = source.index("ALLOY_LITE_BUILD_ARGS =")
        owner_end = source.index("\n\n", owner_start)
        owner = source[owner_start:owner_end]
        for name in (BUILD_FLAG, STAGE_ARG):
            self.assertEqual(
                len(re.findall(rf"--build-arg\s+{name}=", source)),
                1,
            )
            self.assertIn(f"--build-arg {name}=", owner)
        self.assertEqual(source.count("$(ALLOY_LITE_BUILD_ARGS)"), 2)


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
