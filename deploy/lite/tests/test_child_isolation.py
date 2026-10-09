"""What the Lite image gives its non-root children, read from the files that build it.

The runtime starts every bot and agent worker as a non-root uid of its own (core/runtime isolation.py;
its tests run the real drop as root). The image's part: the browser install, the workspace store and
Valkey's data are not theirs to write or read, the tools user the harness looks for exists, each bot
reaches the system PulseAudio and writes its screenshots into its own HOME, and Valkey's password is
never on a command line. The live counterpart is ``child_identities.py`` (``make -C deploy/lite test``).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"
DOCKERFILE = (LITE / "Dockerfile.lite").read_text()
ENTRYPOINT = (LITE / "entrypoint.sh").read_text()
SUPERVISORD = (LITE / "supervisord.conf").read_text()
BOT_LAUNCH = (LITE / "bin" / "vexa-bot-launch").read_text()


def test_the_browser_install_is_not_writable_by_children():
    assert "chmod -R a+rX,go-w /ms-playwright" in DOCKERFILE


def test_the_store_and_valkey_data_are_roots():
    assert "chmod 0700 /var/lib/redis" in DOCKERFILE and "chmod 0755 /workspaces" in DOCKERFILE
    assert "chmod 0700 /var/lib/redis" in ENTRYPOINT and "chmod 0755 /workspaces" in ENTRYPOINT
    assert "chmod 777" not in ENTRYPOINT


def test_the_tools_user_is_the_worker_images():
    worker = (ROOT / "core" / "agent" / "worker" / "Dockerfile").read_text()
    for text in (DOCKERFILE, worker):
        assert "groupadd --system --gid 10001 vexa-tools" in text
        assert re.search(r"useradd --system --uid 10001 --gid 10001 .*vexa-tools", text)


def test_valkeys_password_is_in_a_root_only_file_not_on_its_command_line():
    (command,) = re.findall(r"^command=/usr/local/bin/valkey-server (.*)$", SUPERVISORD, flags=re.M)
    assert "requirepass" not in command and command.startswith("/run/vexa/valkey.conf ")
    block = re.search(r"^\( umask 077; printf 'requirepass.*$", ENTRYPOINT, flags=re.M).group(0)
    assert "/run/vexa/valkey.conf" in block


def test_the_valkey_config_quotes_any_password(tmp_path):
    block = re.search(r"^\( umask 077; printf 'requirepass.*$", ENTRYPOINT, flags=re.M).group(0)
    block = block.replace("/run/vexa/valkey.conf", str(tmp_path / "valkey.conf"))
    subprocess.run(["bash", "-c", block], env={"PATH": "/usr/bin:/bin", "REDIS_PASSWORD": 'a"b\\c'}, check=True)
    assert (tmp_path / "valkey.conf").read_text() == 'requirepass "a\\"b\\\\c"\n'
    assert (tmp_path / "valkey.conf").stat().st_mode & 0o077 == 0


def test_each_bot_writes_screenshots_into_its_own_home_and_reaches_pulseaudio():
    assert "ln -s /proc/self/fd/9 /app/storage/screenshots" in DOCKERFILE
    assert 'exec 9<"$screenshots"' in BOT_LAUNCH and 'screenshots="${HOME:-/tmp}/screenshots"' in BOT_LAUNCH
    assert BOT_LAUNCH.index('exec 9<') < BOT_LAUNCH.index("exec node")
    assert 'PULSE_SERVER="${PULSE_SERVER:-unix:/run/pulse/native}"' in BOT_LAUNCH
    profiles = (ROOT / "core" / "runtime" / "src" / "runtime_kernel" / "profiles.py").read_text()
    assert 'process_groups=("pulse-access",)' in profiles and "usermod -aG pulse-access root" in DOCKERFILE


def test_the_live_check_runs_in_make_test():
    makefile = (LITE / "Makefile").read_text()
    assert 'tests/child_identities.py" || FAIL=1' in makefile
