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


def test_each_bot_writes_screenshots_into_its_own_home():
    assert "ln -s /proc/self/fd/9 /app/storage/screenshots" in DOCKERFILE
    assert 'exec 9<"$screenshots"' in BOT_LAUNCH and 'screenshots="$home/screenshots"' in BOT_LAUNCH
    assert BOT_LAUNCH.index('exec 9<') < BOT_LAUNCH.index("exec node")
    assert 'VEXA_CAPTURE_SIGNAL_DIR="${VEXA_CAPTURE_SIGNAL_DIR:-$home/captured-signal}"' in BOT_LAUNCH


def test_each_bot_runs_its_own_audio_daemon_and_no_shared_one_exists():
    """No system PulseAudio: a bot's daemon, socket in its private HOME, the speak path's graph, no
    module loading once up — no bot reaches another's sink or mic."""
    assert "[program:pulseaudio" not in SUPERVISORD and "pulseaudio" not in SUPERVISORD.split("[group:vexa]")[1]
    assert not (LITE / "bin" / "setup-pulseaudio-sinks.sh").exists()
    assert 'pulse_dir="$home/pulse"' in BOT_LAUNCH and 'mkdir -p -m 0700 "$pulse_dir"' in BOT_LAUNCH
    assert 'export PULSE_SERVER="unix:$pulse_dir/native"' in BOT_LAUNCH
    for piece in ("--system=no", "-n ", "sink_name=tts_sink", "source_name=virtual_mic", "--disallow-module-loading"):
        assert piece in BOT_LAUNCH, piece
    assert BOT_LAUNCH.index("pulseaudio --daemonize=no") < BOT_LAUNCH.index("exec node")
    workload_env = (ROOT / "core" / "runtime" / "src" / "runtime_kernel" / "workload_env.py").read_text()
    plumbing = workload_env.split("PROCESS_PLUMBING_ENV = (", 1)[1].split(")", 1)[0]
    for key in ("DISPLAY", "PULSE_SERVER", "PULSE_SINK", "PULSE_SOURCE", "XDG_RUNTIME_DIR"):
        assert f'"{key}"' not in plumbing, key


def test_the_display_needs_a_cookie_only_bots_can_read():
    (xvfb,) = re.findall(r"^command=Xvfb (.*)$", SUPERVISORD, flags=re.M)
    assert "-ac" not in xvfb.split() and "-auth /run/vexa/display/Xauthority" in xvfb and "-nolisten tcp" in xvfb
    assert "/usr/local/bin/display-cookie /run/vexa/display/Xauthority 99 vexa-display" in ENTRYPOINT
    assert "groupadd --system vexa-display" in DOCKERFILE
    profiles = (ROOT / "core" / "runtime" / "src" / "runtime_kernel" / "profiles.py").read_text()
    assert 'process_groups=("vexa-display",)' in profiles
    assert 'XAUTHORITY="${XAUTHORITY:-/run/vexa/display/Xauthority}"' in BOT_LAUNCH


def test_the_cookie_file_is_root_and_the_display_groups_only(tmp_path, monkeypatch):
    import grp
    import importlib.machinery
    import importlib.util
    import os

    loader = importlib.machinery.SourceFileLoader("display_cookie", str(LITE / "bin" / "display-cookie"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    group = grp.getgrgid(os.getgid()).gr_name
    monkeypatch.setattr(os, "chown", lambda *a: None)
    monkeypatch.setattr(os, "fchown", lambda *a: None)
    path = tmp_path / "display" / "Xauthority"
    assert mod.main(["display-cookie", str(path), "99", group]) == 0
    data = path.read_bytes()
    assert data.count(b"MIT-MAGIC-COOKIE-1") == 2 and (path.stat().st_mode & 0o777) == 0o640
    assert (path.parent.stat().st_mode & 0o777) == 0o750
    first = data
    mod.main(["display-cookie", str(path), "99", group])
    assert path.read_bytes() != first                      # a new cookie every start


def test_vnc_is_off_by_default_loopback_and_password_protected():
    (x11vnc,) = re.findall(r"^command=x11vnc (.*)$", SUPERVISORD, flags=re.M)
    assert "-nopw" not in x11vnc and "-passwdfile /run/vexa/vnc/passwd" in x11vnc and "-localhost" in x11vnc
    (websockify,) = re.findall(r"^command=websockify (.*)$", SUPERVISORD, flags=re.M)
    assert "127.0.0.1:6080" in websockify
    assert SUPERVISORD.count("autostart=%(ENV_VEXA_LITE_VNC)s") == 2
    assert 'export VEXA_LITE_VNC="${VEXA_LITE_VNC:-false}"' in ENTRYPOINT
    assert "unset VEXA_LITE_VNC_PASSWORD" in ENTRYPOINT
    assert "EXPOSE 8056 3001 8100\n" in DOCKERFILE


def test_only_the_credential_file_is_mounted_into_a_root_only_directory():
    makefile = (LITE / "Makefile").read_text()
    assert '-v $$CLAUDE_CREDS:/var/lib/vexa/host-claude/.credentials.json:ro' in makefile
    assert "-v $$HOME/.claude:" not in makefile
    assert "install -d -m 0700 /var/lib/vexa/host-claude" in DOCKERFILE
    assert "chmod 0700 /var/lib/vexa/host-claude" in ENTRYPOINT


def test_workload_logs_live_beside_the_homes_not_in_tmp():
    assert 'PROCESS_LOG_DIR="/var/lib/vexa-runtime/logs"' in SUPERVISORD
    for script in ("probe.sh", "tests/concurrent-bots.sh"):
        assert "/tmp/vexa-workloads" not in (LITE / script).read_text()


def test_the_live_check_runs_in_make_test():
    makefile = (LITE / "Makefile").read_text()
    assert 'tests/child_identities.py" || FAIL=1' in makefile
