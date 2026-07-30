"""ALLOY: prove Lite source identity follows the real Git working tree."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "lite" / "bin" / "source-identity.sh"
POSIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


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
            "deploy/lite/tests/test_source_identity.py",
            "--posix",
        ]
    )
    completed = subprocess.run(command, check=False, timeout=60)
    raise SystemExit(completed.returncode)


def _run(
    *args: str,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=15,
    )


class SourceIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="vexa-lite-source-identity-")
        self.repo = Path(self.temp_dir.name)
        _run("git", "init", "-q", cwd=self.repo)
        _run("git", "config", "user.name", "Vexa Test", cwd=self.repo)
        _run("git", "config", "user.email", "vexa-test@example.invalid", cwd=self.repo)
        project_ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        (self.repo / ".gitignore").write_text(
            f"ignored.txt\n{project_ignore}",
            encoding="utf-8",
        )
        (self.repo / "app.txt").write_text("alpha\n", encoding="utf-8")
        _run("git", "add", ".gitignore", "app.txt", cwd=self.repo)
        committed = _run("git", "commit", "-q", "-m", "fixture", cwd=self.repo)
        if committed.returncode != 0:
            self.fail(committed.stderr)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def identity(self) -> dict[str, object]:
        completed = _run(
            "bash",
            str(SCRIPT),
            "--root",
            str(self.repo),
            "--format",
            "json",
            cwd=self.repo,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_clean_commit_has_stable_clean_identity(self) -> None:
        first = self.identity()
        second = self.identity()
        revision = _run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()

        self.assertEqual(first, second)
        self.assertEqual(first["revision"], revision)
        self.assertEqual(first["dirty"], False)
        self.assertRegex(str(first["fingerprint"]), r"^[0-9a-f]{64}$")

    def test_tracked_change_turns_dirty_and_changes_fingerprint(self) -> None:
        clean = self.identity()
        (self.repo / "app.txt").write_text("beta\n", encoding="utf-8")
        changed = self.identity()

        self.assertEqual(changed["revision"], clean["revision"])
        self.assertEqual(changed["dirty"], True)
        self.assertNotEqual(changed["fingerprint"], clean["fingerprint"])

    def test_staged_and_unstaged_worktree_bytes_drive_fingerprint(self) -> None:
        clean = self.identity()
        (self.repo / "app.txt").write_text("beta\n", encoding="utf-8")
        staged_result = _run("git", "add", "app.txt", cwd=self.repo)
        self.assertEqual(staged_result.returncode, 0, staged_result.stderr)
        staged = self.identity()
        (self.repo / "app.txt").write_text("gamma\n", encoding="utf-8")
        unstaged = self.identity()

        self.assertEqual(staged["dirty"], True)
        self.assertEqual(unstaged["dirty"], True)
        self.assertNotEqual(staged["fingerprint"], clean["fingerprint"])
        self.assertNotEqual(unstaged["fingerprint"], staged["fingerprint"])

    def test_deleted_tracked_file_changes_identity(self) -> None:
        clean = self.identity()
        (self.repo / "app.txt").unlink()
        deleted = self.identity()

        self.assertEqual(deleted["dirty"], True)
        self.assertNotEqual(deleted["fingerprint"], clean["fingerprint"])

    @unittest.skipUnless(os.name != "nt", "symlink identity is a POSIX Git input")
    def test_symlink_target_changes_fingerprint(self) -> None:
        link = self.repo / "source-link"
        link.symlink_to("target-a")
        added = _run("git", "add", "source-link", cwd=self.repo)
        self.assertEqual(added.returncode, 0, added.stderr)
        committed = _run("git", "commit", "-q", "-m", "add symlink", cwd=self.repo)
        self.assertEqual(committed.returncode, 0, committed.stderr)
        clean = self.identity()

        link.unlink()
        link.symlink_to("target-b")
        changed = self.identity()

        self.assertEqual(changed["dirty"], True)
        self.assertNotEqual(changed["fingerprint"], clean["fingerprint"])

    def test_unmerged_index_is_rejected(self) -> None:
        blob = _run("git", "rev-parse", "HEAD:app.txt", cwd=self.repo).stdout.strip()
        removed = _run("git", "update-index", "--force-remove", "app.txt", cwd=self.repo)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        entries = "".join(
            f"100644 {blob} {stage}\tapp.txt\n"
            for stage in (1, 2, 3)
        )
        updated = _run(
            "git",
            "update-index",
            "--index-info",
            cwd=self.repo,
            input_text=entries,
        )
        self.assertEqual(updated.returncode, 0, updated.stderr)

        completed = _run(
            "bash",
            str(SCRIPT),
            "--root",
            str(self.repo),
            "--format",
            "json",
            cwd=self.repo,
        )

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("[ALLOY] source-identity: unmerged index entry: app.txt", completed.stderr)

    def test_nonignored_untracked_file_changes_identity_but_ignored_file_does_not(self) -> None:
        clean = self.identity()
        (self.repo / "ignored.txt").write_text("cache\n", encoding="utf-8")
        ignored = self.identity()
        (self.repo / "new-source.txt").write_text("new source\n", encoding="utf-8")
        untracked = self.identity()

        self.assertEqual(ignored, clean)
        self.assertEqual(untracked["dirty"], True)
        self.assertNotEqual(untracked["fingerprint"], clean["fingerprint"])

    def test_pnpm_store_is_ignored_and_does_not_change_identity(self) -> None:
        clean = self.identity()
        cache_entry = self.repo / ".pnpm-store" / "v11" / "cache-entry"
        cache_entry.parent.mkdir(parents=True)
        cache_entry.write_text("cache\n", encoding="utf-8")
        cached = self.identity()

        self.assertEqual(cached, clean)

    @unittest.skipUnless(os.name != "nt", "executable bit is a POSIX Git input")
    def test_executable_bit_changes_fingerprint(self) -> None:
        clean = self.identity()
        os.chmod(self.repo / "app.txt", 0o755)
        changed = self.identity()

        self.assertEqual(changed["dirty"], True)
        self.assertNotEqual(changed["fingerprint"], clean["fingerprint"])

    @unittest.skipUnless(
        re.match(r"^/mnt/[a-z]/", str(ROOT)),
        "Windows-style worktree pointers are specific to WSL-mounted drives",
    )
    def test_wsl_windows_pointer_preserves_full_identity(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="vexa-lite-windows-worktree-",
            dir=ROOT.parent,
        ) as temp_dir:
            base = Path(temp_dir)
            primary = base / "primary"
            linked = base / "linked"
            primary.mkdir()
            _run("git", "init", "-q", cwd=primary)
            _run("git", "config", "user.name", "Vexa Test", cwd=primary)
            _run("git", "config", "user.email", "vexa-test@example.invalid", cwd=primary)
            _run("git", "config", "core.filemode", "false", cwd=primary)
            (primary / "app.txt").write_text("alpha\n", encoding="utf-8")
            _run("git", "add", "app.txt", cwd=primary)
            committed = _run("git", "commit", "-q", "-m", "fixture", cwd=primary)
            self.assertEqual(committed.returncode, 0, committed.stderr)
            added = _run(
                "git",
                "worktree",
                "add",
                "-q",
                "-b",
                "linked",
                str(linked),
                cwd=primary,
            )
            self.assertEqual(added.returncode, 0, added.stderr)

            posix_clean_completed = _run(
                "bash",
                str(SCRIPT),
                "--root",
                str(linked),
                "--format",
                "json",
                cwd=linked,
            )
            self.assertEqual(
                posix_clean_completed.returncode,
                0,
                posix_clean_completed.stderr,
            )
            posix_clean = json.loads(posix_clean_completed.stdout)

            gitdir = (linked / ".git").read_text(encoding="utf-8").strip().removeprefix("gitdir: ")
            match = re.match(r"^/mnt/([a-z])/(.*)$", gitdir)
            self.assertIsNotNone(match, gitdir)
            drive, tail = match.groups()
            (linked / ".git").write_text(
                f"gitdir: {drive.upper()}:/{tail}\n",
                encoding="utf-8",
            )

            git_exe = next(
                (
                    candidate
                    for candidate in (
                        Path("/mnt/c/Git/bin/git.exe"),
                        Path("/mnt/c/Program Files/Git/cmd/git.exe"),
                    )
                    if candidate.exists()
                ),
                None,
            )
            if git_exe is None:
                self.skipTest("Windows Git is unavailable for cross-Git stat-cache coverage")
            linked_match = re.match(r"^/mnt/([a-z])/(.*)$", str(linked.resolve()))
            self.assertIsNotNone(linked_match, str(linked))
            linked_drive, linked_tail = linked_match.groups()
            linked_tail = linked_tail.replace("/", "\\")
            linked_windows = f"{linked_drive.upper()}:\\{linked_tail}"
            refreshed = _run(
                str(git_exe),
                "-C",
                linked_windows,
                "status",
                "--short",
                cwd=linked,
            )
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertEqual(refreshed.stdout, "")

            windows_clean_completed = _run(
                "bash",
                str(SCRIPT),
                "--root",
                str(linked),
                "--format",
                "json",
                cwd=linked,
            )
            self.assertEqual(
                windows_clean_completed.returncode,
                0,
                windows_clean_completed.stderr,
            )
            windows_clean = json.loads(windows_clean_completed.stdout)
            self.assertEqual(windows_clean, posix_clean)
            self.assertEqual(windows_clean["dirty"], False)

            (linked / "app.txt").write_text("beta\n", encoding="utf-8")
            windows_dirty_completed = _run(
                "bash",
                str(SCRIPT),
                "--root",
                str(linked),
                "--format",
                "json",
                cwd=linked,
            )
            self.assertEqual(
                windows_dirty_completed.returncode,
                0,
                windows_dirty_completed.stderr,
            )
            windows_dirty = json.loads(windows_dirty_completed.stdout)
            revision = _run("git", "rev-parse", "HEAD", cwd=primary).stdout.strip()
            self.assertEqual(windows_dirty["revision"], revision)
            self.assertEqual(windows_dirty["dirty"], True)
            self.assertNotEqual(
                windows_dirty["fingerprint"],
                windows_clean["fingerprint"],
            )


if __name__ == "__main__":
    if os.name == "nt" and "--posix" not in sys.argv:
        _reexec_in_wsl()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
