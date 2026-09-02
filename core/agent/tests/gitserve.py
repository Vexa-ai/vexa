"""gitserve — a LOCAL bare repo served over git's real ssh transport, for tests.

The repository field is validated now (``control_plane.repo_ref``), and a bare filesystem path is not
a repository URL — deliberately, because accepting one let any caller name another user's workspace
directory in the shared store and clone it. So a ROUTE test can no longer pass ``str(some_tmp_dir)``.

Rather than weaken the gate for tests, this serves the same local bare repo through the transport a
real ``ssh://`` URL uses: ``ssh`` is stubbed with a script that drops its options and its host, maps
the server-side path, and runs ``git-upload-pack`` locally. The clone is real, the URL shape is real,
git's own ssh code path is exercised, and nothing touches a network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def bare_repo(tmp_path: Path, name: str = "kg", **files: str) -> Path:
    """A real bare repo with one commit. ``files`` overrides/adds working-tree files."""
    work = tmp_path / f"{name}-src"
    work.mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(work), *a], check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    content = {"CLAUDE.md": "# governed workspace\n", "README.md": "the existing repo\n", **files}
    for rel, body in content.items():
        f = work / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    g("add", "-A"); g("commit", "-q", "-m", "their history")
    bare = tmp_path / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return bare


def serve(tmp_path: Path, bare: Path, monkeypatch, *, owner: str = "acme", repo: str = "kg") -> str:
    """Install the ssh stub and return the ``ssh://`` URL that reaches ``bare``.

    ``GIT_SSH_COMMAND`` is set in the process env: ``shared.gitenv.scrubbed_git_env`` copies the
    environment (it strips only git's repo-DISCOVERY vars), so this reaches the clone exactly as a
    deploy key's would — which is also what makes it a faithful stand-in."""
    script = tmp_path / "fake-ssh"
    script.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -i|-o|-p|-l|-F) shift 2 ;;\n"
        "    -*) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "shift\n"                                    # drop the host
        f"cmd=$(printf '%s' \"$*\" | sed \"s#/{owner}/{repo}\\\\.git#{bare}#g\")\n"
        'exec sh -c "$cmd"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
    return f"ssh://git@fake-host/{owner}/{repo}.git"
