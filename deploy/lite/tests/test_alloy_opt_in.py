"""ALLOY: exercise Lite opt-in defaults and precedence without invoking Docker."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "deploy" / "lite" / "bin" / "vexa-bot-launch"
ENTRYPOINT = ROOT / "deploy" / "lite" / "entrypoint.sh"
DOCKERFILE = ROOT / "deploy" / "lite" / "Dockerfile.lite"
MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"
RUNTIME_DEFAULTS = {
    "ALLOY_STT_MAX_CONCURRENCY": "0",
    "ALLOY_STT_CHANNEL_BACKPRESSURE": "0",
    "ALLOY_STT_LANGUAGE_MODE": "configured",
    "ALLOY_STT_TELEMETRY": "0",
}
LOCAL_PROFILE = {
    "ALLOY_STT_MAX_CONCURRENCY": "1",
    "ALLOY_STT_CHANNEL_BACKPRESSURE": "1",
    "ALLOY_STT_LANGUAGE_MODE": "auto",
    "ALLOY_STT_TELEMETRY": "1",
}
BUILD_DEFAULTS = {
    "ALLOY_SKIP_HF_CACHE_WARM": "0",
    "ALLOY_LITE_BUNDLED_PYTHON": "0",
    "NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT": "0",
}
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _wsl_path(path: Path) -> str:
    resolved = PureWindowsPath(path.resolve())
    drive = resolved.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{'/'.join(resolved.parts[1:])}"


def _reexec_in_wsl() -> None:
    command = [
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
        "deploy/lite/tests/test_alloy_opt_in.py",
        "--posix",
    ]
    completed = subprocess.run(command, check=False, timeout=60)
    raise SystemExit(completed.returncode)


def _controlled_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "PATH": POSIX_PATH,
        "HOME": "/tmp",
        "LC_ALL": "C",
        **(extra or {}),
    }


def _run_bash(script: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=_controlled_env(env),
        capture_output=True,
        check=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _real_export_values(
    path: Path,
    names: tuple[str, ...],
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Execute the named export assignments exactly as written in the real script."""
    source = path.read_text(encoding="utf-8")
    assignments: list[str] = []
    for name in names:
        matches = [
            line
            for line in source.splitlines()
            if re.match(rf"^export {re.escape(name)}=", line)
        ]
        if len(matches) != 1:
            raise AssertionError(f"{path}: expected one export for {name}, got {matches}")
        assignments.append(matches[0])
    printf = "printf '%s\\n' " + " ".join(f'"${{{name}}}"' for name in names)
    values = _run_bash("\n".join([*assignments, printf]), env).splitlines()
    return dict(zip(names, values, strict=True))


def _make_dry_run_with_env_file(
    target: str,
    env_file: Path,
    *,
    ambient: dict[str, str] | None = None,
    make_vars: dict[str, str] | None = None,
) -> str:
    args = [
        "make",
        "--no-print-directory",
        "-n",
        "-f",
        str(MAKEFILE),
        f"ROOT={ROOT}",
        f"ENV_FILE={env_file}",
        *(f"{key}={value}" for key, value in (make_vars or {}).items()),
        target,
    ]
    return subprocess.run(
        args,
        cwd=ROOT,
        env=_controlled_env(ambient),
        capture_output=True,
        check=True,
        text=True,
        timeout=15,
    ).stdout


def _make_dry_run(
    target: str,
    *,
    env_text: str = "",
    ambient: dict[str, str] | None = None,
    make_vars: dict[str, str] | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="vexa-alloy-opt-in-") as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text(env_text, encoding="utf-8")
        return _make_dry_run_with_env_file(
            target,
            env_file,
            ambient=ambient,
            make_vars=make_vars,
        )


def _runtime_values_from_dry_run(
    *,
    env_text: str = "",
    ambient: dict[str, str] | None = None,
    make_vars: dict[str, str] | None = None,
) -> dict[str, str]:
    """Execute only the resolver fragment printed by the real `make -n up` recipe."""
    with tempfile.TemporaryDirectory(prefix="vexa-alloy-opt-in-") as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text(env_text, encoding="utf-8")
        dry_run = _make_dry_run_with_env_file(
            "up",
            env_file,
            ambient=ambient,
            make_vars=make_vars,
        )
        start = dry_run.find("envv() {")
        end = dry_run.find("IMAGE_TAG=", start)
        if start < 0 or end < 0:
            raise AssertionError(f"Lite up dry-run did not expose its resolver boundary:\n{dry_run}")
        resolver = dry_run[start:end].replace("\\\n", "\n")
        if "docker" in resolver:
            raise AssertionError(f"unsafe command entered resolver-only test fragment:\n{resolver}")
        names = tuple(RUNTIME_DEFAULTS)
        printf = "\n".join(
            f"printf '{name}=%s\\n' \"${{{name}}}\""
            for name in names
        )
        output = _run_bash(
            f"{resolver}\n{printf}",
            {
                **(ambient or {}),
                # GNU make exports command-line variables to its recipe. Mirror that environment
                # while executing the exact resolver fragment extracted from the dry-run.
                **(make_vars or {}),
            },
        )
        return dict(line.split("=", 1) for line in output.splitlines())


def _build_args(dry_run: str) -> dict[str, str]:
    found = dict(
        re.findall(
            r"--build-arg\s+"
            r"(ALLOY_SKIP_HF_CACHE_WARM|ALLOY_LITE_BUNDLED_PYTHON|"
            r"NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT)"
            r"=([^\s\\]+)",
            dry_run,
        )
    )
    return found


class AlloyLiteOptInTest(unittest.TestCase):
    def test_bot_builder_creates_hf_cache_before_warm_switch_and_final_copy(self) -> None:
        source = DOCKERFILE.read_text(encoding="utf-8")
        bot_builder_start = source.index(" AS bot-builder")
        bot_builder_end = source.index("\nFROM ", bot_builder_start)
        cache_create = re.search(
            r"(?m)^RUN mkdir -p /opt/hf-cache\s*$",
            source[bot_builder_start:bot_builder_end],
        )
        self.assertIsNotNone(
            cache_create,
            "bot-builder must create /opt/hf-cache unconditionally",
        )
        cache_create_offset = bot_builder_start + cache_create.start()
        warm_switch = source.index(
            'RUN if [ "$ALLOY_SKIP_HF_CACHE_WARM" = "1" ]',
            bot_builder_start,
            bot_builder_end,
        )
        final_copy = source.index(
            "COPY --from=bot-builder /opt/hf-cache /opt/hf-cache",
            bot_builder_end,
        )
        self.assertLess(cache_create_offset, warm_switch)
        self.assertLess(warm_switch, bot_builder_end)
        self.assertLess(bot_builder_end, final_copy)

    def test_launcher_absent_flags_restore_upstream_behavior(self) -> None:
        self.assertEqual(
            _real_export_values(LAUNCHER, tuple(RUNTIME_DEFAULTS)),
            RUNTIME_DEFAULTS,
        )

    def test_launcher_explicit_local_profile_survives_unchanged(self) -> None:
        self.assertEqual(
            _real_export_values(LAUNCHER, tuple(LOCAL_PROFILE), LOCAL_PROFILE),
            LOCAL_PROFILE,
        )

    def test_entrypoint_telemetry_is_opt_in_and_preserves_explicit_one(self) -> None:
        self.assertEqual(
            _real_export_values(ENTRYPOINT, ("ALLOY_STT_TELEMETRY",)),
            {"ALLOY_STT_TELEMETRY": "0"},
        )
        self.assertEqual(
            _real_export_values(
                ENTRYPOINT,
                ("ALLOY_STT_TELEMETRY",),
                {"ALLOY_STT_TELEMETRY": "1"},
            ),
            {"ALLOY_STT_TELEMETRY": "1"},
        )

    def test_make_runtime_precedence_is_explicit_then_env_file_then_default(self) -> None:
        env_profile = "\n".join(f"{key}={value}" for key, value in LOCAL_PROFILE.items())
        alternate_profile = {
            "ALLOY_STT_MAX_CONCURRENCY": "3",
            "ALLOY_STT_CHANNEL_BACKPRESSURE": "0",
            "ALLOY_STT_LANGUAGE_MODE": "configured",
            "ALLOY_STT_TELEMETRY": "0",
        }
        empty_env = "\n".join(f"{key}=" for key in RUNTIME_DEFAULTS)

        cases = [
            ("safe defaults", "", None, None, RUNTIME_DEFAULTS),
            ("non-empty .env", env_profile, None, None, LOCAL_PROFILE),
            (
                "ambient overrides .env",
                env_profile,
                alternate_profile,
                None,
                alternate_profile,
            ),
            (
                "make command line overrides .env",
                env_profile,
                None,
                alternate_profile,
                alternate_profile,
            ),
            (
                "empty .env cannot erase ambient",
                empty_env,
                LOCAL_PROFILE,
                None,
                LOCAL_PROFILE,
            ),
            ("empty .env falls back safely", empty_env, None, None, RUNTIME_DEFAULTS),
        ]
        for name, env_text, ambient, make_vars, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    _runtime_values_from_dry_run(
                        env_text=env_text,
                        ambient=ambient,
                        make_vars=make_vars,
                    ),
                    expected,
                )

    def test_build_and_push_dry_runs_share_safe_default_build_args(self) -> None:
        build = _build_args(_make_dry_run("build"))
        push = _build_args(_make_dry_run("push"))
        self.assertEqual(build, BUILD_DEFAULTS)
        self.assertEqual(push, BUILD_DEFAULTS)
        self.assertEqual(push, build)

    def test_build_args_share_one_scoped_env_file_aware_owner(self) -> None:
        source = MAKEFILE.read_text(encoding="utf-8")
        start = source.index("ALLOY_LITE_BUILD_ARGS =")
        end = source.index("\n\n", start)
        build_args_owner = source[start:end]
        resolver_calls = re.findall(
            r"\$\(call\s+([A-Za-z0-9_-]+),"
            r"(ALLOY_SKIP_HF_CACHE_WARM|NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT)\)",
            build_args_owner,
        )
        self.assertEqual(
            resolver_calls,
            [
                ("alloy_build_value", "ALLOY_SKIP_HF_CACHE_WARM"),
                ("alloy_build_value", "NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT"),
            ],
        )
        resolver_start = source.index("alloy_build_value =")
        resolver_end = source.index("\n\n", resolver_start)
        resolver = source[resolver_start:resolver_end]
        self.assertIn("$(ENV_FILE)", resolver)
        self.assertIn("$($(1))", resolver)
        self.assertNotRegex(source, r"(?m)^\s*-?include\s+\$\(ENV_FILE\)")

    def test_build_and_push_dry_runs_preserve_explicit_build_args(self) -> None:
        explicit = {key: "1" for key in BUILD_DEFAULTS}
        build = _build_args(_make_dry_run("build", make_vars=explicit))
        push = _build_args(_make_dry_run("push", make_vars=explicit))
        self.assertEqual(build, explicit)
        self.assertEqual(push, explicit)
        self.assertEqual(push, build)

    def test_build_and_push_dry_runs_use_build_flag_precedence(self) -> None:
        enabled = {key: "1" for key in BUILD_DEFAULTS}
        disabled = BUILD_DEFAULTS
        enabled_env = "\n".join(f"{key}=1" for key in BUILD_DEFAULTS)
        disabled_env = "\n".join(f"{key}=0" for key in BUILD_DEFAULTS)
        empty_env = "\n".join(f"{key}=" for key in BUILD_DEFAULTS)
        cases = [
            ("non-empty .env", enabled_env, None, None, enabled),
            ("ambient overrides .env", enabled_env, disabled, None, disabled),
            (
                "make command line overrides .env",
                disabled_env,
                None,
                enabled,
                enabled,
            ),
            (
                "empty .env cannot erase ambient",
                empty_env,
                enabled,
                None,
                enabled,
            ),
            ("empty .env falls back safely", empty_env, None, None, BUILD_DEFAULTS),
        ]
        for target in ("build", "push"):
            for name, env_text, ambient, make_vars, expected in cases:
                with self.subTest(target=target, name=name):
                    self.assertEqual(
                        _build_args(
                            _make_dry_run(
                                target,
                                env_text=env_text,
                                ambient=ambient,
                                make_vars=make_vars,
                            )
                        ),
                        expected,
                    )


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
