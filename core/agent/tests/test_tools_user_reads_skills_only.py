"""As root, with the real tools user: the skills a turn loads are the tools user's to read, not to
change. It cannot edit a staged skill, add one to the stage, or re-point ``~/.claude/skills`` — and
it can still read every staged ``SKILL.md``. Skips without a root worker and the image's tools user;
run it in the worker image (see ``test_tools_user_cannot_swap_git.py``)."""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from llm import ports
from llm.claude_skills import _link_skills_into_home


def _tools_user_present() -> bool:
    try:
        pwd.getpwnam(ports.TOOLS_USER)
    except KeyError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0 or not _tools_user_present(),
    reason="needs a root worker and the image's tools user")

_AS_TOOLS = r'''
import json, os, sys
home = sys.argv[1]
stage = os.path.realpath(os.path.join(home, ".claude", "skills"))
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "ok"
    except OSError as exc:
        out[name] = exc.__class__.__name__
def write(path, text, mode="w"):
    with open(path, mode) as fh:
        fh.write(text)
attempt("read_staged_skill", lambda: open(os.path.join(stage, "probe", "SKILL.md")).read())
attempt("edit_staged_skill", lambda: write(os.path.join(stage, "probe", "SKILL.md"), "allowed-tools: Bash\n", "a"))
attempt("add_to_stage", lambda: os.mkdir(os.path.join(stage, "planted")))
attempt("unlink_skills_link", lambda: os.unlink(os.path.join(home, ".claude", "skills")))
attempt("plant_in_user_scope", lambda: write(os.path.join(home, ".claude", "planted.json"), "{}"))
attempt("plant_in_stages", lambda: os.mkdir(os.path.join(home, ".vexa-skills", "turn-planted")))
print(json.dumps(out))
'''


def test_the_tools_user_reads_the_skills_and_cannot_change_them(monkeypatch):
    base = Path(tempfile.mkdtemp(prefix="vexa-skills-ro-"))
    try:
        os.chmod(base, 0o755)
        home, work = base / "home", base / "ws"
        home.mkdir()
        (work / "skills" / "probe").mkdir(parents=True)
        (work / "skills" / "probe" / "SKILL.md").write_text("---\nname: probe\ndescription: p\n---\n")
        monkeypatch.setenv("HOME", str(home))
        _link_skills_into_home(work)
        assert (home / ".claude" / "skills").is_symlink()
        assert ports.show_tools([home / ".claude", home / ".vexa-skills"]) is True
        got = json.loads(subprocess.run([sys.executable, "-c", _AS_TOOLS, str(home)],
                                        capture_output=True, text=True, check=True,
                                        **ports.harness_identity_kwargs()).stdout)
        assert got["read_staged_skill"] == "ok", got
        for step in ("edit_staged_skill", "add_to_stage", "unlink_skills_link",
                     "plant_in_user_scope", "plant_in_stages"):
            assert got[step] == "PermissionError", (step, got)
        assert (home / ".claude" / "skills").is_symlink()
    finally:
        shutil.rmtree(base, ignore_errors=True)
