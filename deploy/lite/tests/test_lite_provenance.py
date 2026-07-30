"""ALLOY: generated-command contracts for source-bound Vexa Lite launches."""
from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
ROOT_MAKEFILE = ROOT / "Makefile"
LITE_MAKEFILE = ROOT / "deploy" / "lite" / "Makefile"
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REVISION = "1234567890abcdef1234567890abcdef12345678"
FINGERPRINT = "ab" * 32
IMAGE_ID = "sha256:" + ("cd" * 32)
CONTAINER_ID = "ef" * 32
RUNNER = ROOT / "deploy" / "lite" / "bin" / "provenance.sh"


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
            "deploy/lite/tests/test_lite_provenance.py",
            "--posix",
        ]
    )
    completed = subprocess.run(command, check=False, timeout=60)
    raise SystemExit(completed.returncode)


def _controlled_env() -> dict[str, str]:
    return {"PATH": POSIX_PATH, "HOME": "/tmp", "LC_ALL": "C"}


def _dry_run(
    makefile: Path,
    target: str,
    *,
    make_vars: dict[str, str] | None = None,
    env_text: str = "",
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="vexa-lite-provenance-") as temp_dir:
        env_file = Path(temp_dir) / ".env"
        env_file.write_text(env_text, encoding="utf-8")
        return subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "-f",
                str(makefile),
                f"ROOT={ROOT}",
                f"ENV_FILE={env_file}",
                *(f"{key}={value}" for key, value in (make_vars or {}).items()),
                target,
            ],
            cwd=ROOT,
            env=_controlled_env(),
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )


class LiteProvenanceMakeTest(unittest.TestCase):
    def test_root_exposes_explicit_provenance_lifecycle_targets(self) -> None:
        expected = {
            "lite-dev": ("dev", "ALLOY_LITE_PROVENANCE=1"),
            "lite-published": ("published", "ALLOY_LITE_PROVENANCE=1"),
            "lite-status": ("status", "FORMAT=json"),
            "lite-down": ("down", None),
        }
        for target, (nested, token) in expected.items():
            with self.subTest(target=target):
                make_vars = {"FORMAT": "json"} if target == "lite-status" else None
                completed = _dry_run(ROOT_MAKEFILE, target, make_vars=make_vars)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("-C deploy/lite", completed.stdout)
                self.assertIn(nested, completed.stdout)
                self.assertIn(f'ROOT="{ROOT}"', completed.stdout)
                if token:
                    self.assertIn(token, completed.stdout)

    def test_provenance_build_stamps_exact_source_identity(self) -> None:
        completed = _dry_run(
            LITE_MAKEFILE,
            "build",
            make_vars={
                "ALLOY_LITE_PROVENANCE": "1",
                "SOURCE_REVISION": REVISION,
                "SOURCE_FINGERPRINT": FINGERPRINT,
                "SOURCE_DIRTY": "1",
                "TAG": "alloy-dev-test",
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            f"--label org.opencontainers.image.revision={REVISION}",
            completed.stdout,
        )
        self.assertIn(
            f"--label ai.vexa.source.fingerprint={FINGERPRINT}",
            completed.stdout,
        )
        self.assertIn("--label ai.vexa.source.dirty=1", completed.stdout)
        self.assertIn("-t vexa-lite:alloy-dev-test", completed.stdout)

    def test_guarded_up_uses_exact_image_and_never_probes_legacy_dev_tag(self) -> None:
        completed = _dry_run(
            LITE_MAKEFILE,
            "up",
            make_vars={
                "ALLOY_LITE_PROVENANCE": "1",
                "APP_IMAGE": IMAGE_ID,
                "LITE_MODE": "dev",
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("fatal: not a git repository", completed.stderr)
        self.assertIn(f'IMG="{IMAGE_ID}"', completed.stdout)
        self.assertNotIn("docker image inspect vexa-lite:dev", completed.stdout)
        self.assertIn("--label ai.vexa.lite.mode=dev", completed.stdout)
        self.assertIn(f"--label ai.vexa.lite.expected-image={IMAGE_ID}", completed.stdout)

    def test_flag_off_preserves_legacy_local_image_precedence(self) -> None:
        completed = _dry_run(LITE_MAKEFILE, "up")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("docker image inspect vexa-lite:dev", completed.stdout)
        self.assertNotIn("--label ai.vexa.lite.mode=", completed.stdout)

    def test_invalid_opt_in_values_preserve_legacy_path(self) -> None:
        for value in ("0", "true", "yes"):
            with self.subTest(value=value):
                completed = _dry_run(
                    LITE_MAKEFILE,
                    "up",
                    make_vars={"ALLOY_LITE_PROVENANCE": value},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("docker image inspect vexa-lite:dev", completed.stdout)
                self.assertNotIn("--label ai.vexa.lite.mode=", completed.stdout)

    def test_exact_opt_in_requires_nonempty_app_image_before_docker_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vexa-lite-make-guard-") as temp_dir:
            temp = Path(temp_dir)
            env_file = temp / ".env"
            env_file.write_text("", encoding="utf-8")
            log = temp / "docker.log"
            fake_docker = temp / "docker"
            fake_docker.write_text(
                """#!/usr/bin/env bash
printf 'docker' >> "$FAKE_LOG"
printf ' <%s>' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
""",
                encoding="utf-8",
                newline="\n",
            )
            fake_docker.chmod(0o755)
            env = {
                **_controlled_env(),
                "PATH": f"{temp}:{POSIX_PATH}",
                "FAKE_LOG": str(log),
            }

            completed = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    str(LITE_MAKEFILE),
                    f"ROOT={ROOT}",
                    f"ENV_FILE={env_file}",
                    "ALLOY_LITE_PROVENANCE=1",
                    "up",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                check=False,
                text=True,
                timeout=20,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn(
                "[ALLOY] APP_IMAGE is required for provenance mode",
                completed.stdout,
            )
            self.assertNotIn(" <run>", log.read_text(encoding="utf-8"))

    def test_down_removes_only_lite_containers_and_network(self) -> None:
        completed = _dry_run(LITE_MAKEFILE, "down")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "docker rm -f vexa-lite vexa-lite-postgres vexa-lite-minio vexa-lite-whisper",
            completed.stdout,
        )
        self.assertIn("docker network rm vexa-lite-net", completed.stdout)
        self.assertNotIn("docker volume rm", completed.stdout)
        self.assertNotIn("docker image rm", completed.stdout)
        self.assertNotIn("docker rmi", completed.stdout)

    def test_env_file_cannot_silently_enable_provenance_mode(self) -> None:
        completed = _dry_run(
            LITE_MAKEFILE,
            "up",
            env_text="ALLOY_LITE_PROVENANCE=1\n",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("docker image inspect vexa-lite:dev", completed.stdout)
        self.assertNotIn("--label ai.vexa.lite.mode=", completed.stdout)


class LiteProvenanceRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="vexa-lite-runner-")
        self.temp = Path(self.temp_dir.name)
        self.log = self.temp / "calls.log"
        self.counter = self.temp / "identity-count"
        self.fake_make = self.temp / "make"
        self.fake_docker = self.temp / "docker"
        self.fake_identity = self.temp / "source-identity"
        self.env_file = self.temp / ".env"
        self.env_file.write_text("IMAGE_TAG=v012\n", encoding="utf-8")
        self._write_executable(
            self.fake_make,
            """#!/usr/bin/env bash
printf 'make' >> "$FAKE_LOG"
printf ' <%s>' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
""",
        )
        self._write_executable(
            self.fake_identity,
            """#!/usr/bin/env bash
count=0
[[ -f "$IDENTITY_COUNTER" ]] && count="$(cat "$IDENTITY_COUNTER")"
count=$((count + 1))
printf '%s' "$count" > "$IDENTITY_COUNTER"
fingerprint="$FAKE_FINGERPRINT"
if [[ "$count" -eq 2 && -n "${FAKE_SECOND_FINGERPRINT:-}" ]]; then
  fingerprint="$FAKE_SECOND_FINGERPRINT"
fi
printf 'SOURCE_REVISION=%s\\n' "$FAKE_REVISION"
printf 'SOURCE_DIRTY=%s\\n' "$FAKE_DIRTY"
printf 'SOURCE_FINGERPRINT=%s\\n' "$fingerprint"
""",
        )
        self._write_executable(
            self.fake_docker,
            """#!/usr/bin/env bash
printf 'docker' >> "$FAKE_LOG"
printf ' <%s>' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
joined="$*"
if [[ "${FAKE_CONTAINER_MISSING:-0}" == "1" && "$joined" == inspect* ]]; then
  exit 1
fi
case "$joined" in
  "pull "*) exit 0 ;;
  'inspect --format {{.Id}} vexa-lite') printf '%s\\n' "$FAKE_CONTAINER_ID" ;;
  *'{{.Id}}'*) printf '%s\\n' "${FAKE_EXPECTED_IMAGE_ID:-$FAKE_IMAGE_ID}" ;;
  *'org.opencontainers.image.revision'*) printf '%s\\n' "$FAKE_REVISION" ;;
  *'ai.vexa.source.fingerprint'*) printf '%s\\n' "${FAKE_IMAGE_FINGERPRINT:-$FAKE_FINGERPRINT}" ;;
  *'ai.vexa.source.dirty'*) printf '%s\\n' "$FAKE_DIRTY" ;;
  *'RepoDigests'*) printf '%s\\n' "$FAKE_REPO_DIGEST" ;;
  *'.State.Health'*) printf '%s\\n' "$FAKE_HEALTH" ;;
  *'.State.Running'*) printf '%s\\n' "${FAKE_RUNNING:-true}" ;;
  *'{{.Image}}'*) printf '%s\\n' "$FAKE_IMAGE_ID" ;;
  *'ai.vexa.lite.mode'*) printf '%s\\n' "$FAKE_MODE" ;;
  *'ai.vexa.lite.expected-image'*) printf '%s\\n' "$FAKE_EXPECTED_IMAGE" ;;
  *) printf 'unexpected fake docker call: %s\\n' "$joined" >&2; exit 9 ;;
esac
""",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def runner_env(self, **overrides: str) -> dict[str, str]:
        repo_digest = "vexaai/vexa-lite@" + ("ef" * 32)
        return {
            **_controlled_env(),
            "ROOT": str(ROOT),
            "ENV_FILE": str(self.env_file),
            "SOURCE_IDENTITY_BIN": str(self.fake_identity),
            "MAKE_BIN": str(self.fake_make),
            "DOCKER_BIN": str(self.fake_docker),
            "FAKE_LOG": str(self.log),
            "IDENTITY_COUNTER": str(self.counter),
            "FAKE_REVISION": REVISION,
            "FAKE_DIRTY": "1",
            "FAKE_FINGERPRINT": FINGERPRINT,
            "FAKE_IMAGE_ID": IMAGE_ID,
            "FAKE_CONTAINER_ID": CONTAINER_ID,
            "FAKE_MODE": "dev",
            "FAKE_EXPECTED_IMAGE": IMAGE_ID,
            "FAKE_HEALTH": "healthy",
            "FAKE_RUNNING": "true",
            "FAKE_REPO_DIGEST": repo_digest,
            "DOCKERHUB_USER": "vexaai",
            "IMAGE_NAME": "vexa-lite",
            "APP_CONTAINER": "vexa-lite",
            **overrides,
        }

    def run_runner(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        format_: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = ["bash", str(RUNNER), command]
        if format_:
            args.extend(["--format", format_])
        return subprocess.run(
            args,
            cwd=ROOT,
            env=env or self.runner_env(),
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )

    def test_runner_diagnostics_are_alloy_prefixed(self) -> None:
        completed = self.run_runner("unknown")

        self.assertEqual(completed.returncode, 2)
        self.assertIn("[ALLOY] provenance: expected dev, published, or status", completed.stderr)

    def test_dev_builds_identity_then_runs_the_exact_image_id(self) -> None:
        completed = self.run_runner("dev")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        calls = self.log.read_text(encoding="utf-8")

        self.assertIn("build", calls)
        self.assertIn(f"<SOURCE_REVISION={REVISION}>", calls)
        self.assertIn(f"<SOURCE_FINGERPRINT={FINGERPRINT}>", calls)
        self.assertIn("<SOURCE_DIRTY=1>", calls)
        self.assertIn(f"<TAG=alloy-dev-{REVISION}-{FINGERPRINT}>", calls)
        self.assertIn("<up>", calls)
        self.assertIn(f"<ROOT={ROOT}>", calls)
        self.assertIn(f"<ENV_FILE={self.env_file}>", calls)
        self.assertIn(f"<APP_IMAGE={IMAGE_ID}>", calls)
        self.assertIn("<LITE_MODE=dev>", calls)
        self.assertIn("[ALLOY] Lite provenance: MATCH", completed.stdout)

    def test_dev_stops_before_up_when_source_changes_during_build(self) -> None:
        changed = "12" * 32
        completed = self.run_runner(
            "dev",
            env=self.runner_env(FAKE_SECOND_FINGERPRINT=changed),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("source changed during build", completed.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("<build>", calls)
        self.assertNotIn("<up>", calls)

    def test_dev_stops_before_up_when_built_image_labels_mismatch(self) -> None:
        completed = self.run_runner(
            "dev",
            env=self.runner_env(FAKE_IMAGE_FINGERPRINT="34" * 32),
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("built image labels do not match", completed.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("<build>", calls)
        self.assertNotIn("<up>", calls)

    def test_published_pulls_and_runs_the_resolved_repo_digest(self) -> None:
        digest = "vexaai/vexa-lite@" + ("ef" * 32)
        completed = self.run_runner(
            "published",
            env=self.runner_env(
                FAKE_MODE="published",
                FAKE_EXPECTED_IMAGE=digest,
            ),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("docker <pull> <vexaai/vexa-lite:v012>", calls)
        self.assertIn(f"<APP_IMAGE={digest}>", calls)
        self.assertIn("<LITE_MODE=published>", calls)
        self.assertIn("[ALLOY] Lite provenance: MATCH", completed.stdout)

    def test_published_stops_before_up_when_repo_digest_is_missing(self) -> None:
        completed = self.run_runner(
            "published",
            env=self.runner_env(FAKE_REPO_DIGEST=""),
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("published image has no RepoDigest", completed.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("docker <pull> <vexaai/vexa-lite:v012>", calls)
        self.assertNotIn("<up>", calls)

    def test_status_json_reports_stale_source_identity(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_IMAGE_FINGERPRINT="34" * 32),
            format_="json",
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "STALE")
        self.assertEqual(payload["mode"], "dev")
        self.assertEqual(payload["image_id"], IMAGE_ID)
        self.assertEqual(payload["container_id"], CONTAINER_ID)

    def test_status_json_reports_stale_when_expected_image_differs_from_container(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_EXPECTED_IMAGE_ID="sha256:" + ("12" * 32)),
            format_="json",
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "STALE")
        self.assertEqual(payload["expected_image"], IMAGE_ID)
        self.assertEqual(payload["image_id"], IMAGE_ID)

    def test_status_json_match_reports_runtime_identity_and_health(self) -> None:
        completed = self.run_runner("status", format_="json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "MATCH")
        self.assertEqual(payload["mode"], "dev")
        self.assertEqual(payload["expected_image"], IMAGE_ID)
        self.assertEqual(payload["image_id"], IMAGE_ID)
        self.assertEqual(payload["container_id"], CONTAINER_ID)
        self.assertEqual(payload["health"], "healthy")

    def test_status_json_rejects_legacy_container(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_MODE="", FAKE_EXPECTED_IMAGE=""),
            format_="json",
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["verdict"], "LEGACY")

    def test_status_json_reports_legacy_when_container_is_missing(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_CONTAINER_MISSING="1"),
            format_="json",
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "LEGACY")
        self.assertEqual(payload["health"], "missing")
        self.assertEqual(payload["container_id"], "")

    def test_status_json_rejects_unhealthy_container(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_HEALTH="unhealthy"),
            format_="json",
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["verdict"], "UNHEALTHY")

    def test_status_json_rejects_stopped_container(self) -> None:
        completed = self.run_runner(
            "status",
            env=self.runner_env(FAKE_RUNNING="false", FAKE_HEALTH="stopped"),
            format_="json",
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "UNHEALTHY")
        self.assertEqual(payload["health"], "stopped")


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
