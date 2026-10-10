"""What the Lite image gives its non-root children, read from the files that build it.

The runtime starts every bot and agent worker as a non-root uid of its own (core/runtime isolation.py;
its tests run the real drop as root). The image's part: the browser install, the workspace store and
Valkey's data are not theirs to write or read, the tools user the harness looks for exists, each bot
brings up its own X display and audio daemon and writes its screenshots into its own HOME, and
Valkey's password is never on a command line. The live counterparts are ``child_identities.py``
(``make -C deploy/lite test``) and ``bot_displays.py`` (run by ``tests/concurrent-bots.sh``).
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
    (command,) = re.findall(r"^command=/usr/local/bin/no-user-namespaces /usr/local/bin/valkey-server (.*)$",
                            SUPERVISORD, flags=re.M)
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


def test_roots_runtime_directory_gets_its_mode_before_anything_writes_there():
    """/run/vexa holds the rendered supervisor config, Valkey's config and the self-host keys. Its mode
    is set (not just at creation: a restarted container keeps the directory) before the first write."""
    line = "mkdir -p /run/vexa && chown root:root /run/vexa && chmod 0700 /run/vexa"
    assert line in ENTRYPOINT
    first = ENTRYPOINT.index(line)
    for writer in ("/usr/local/bin/provision-key.sh", "python3 /usr/local/bin/render-supervisord",
                   "/run/vexa/valkey.conf"):
        assert first < ENTRYPOINT.index(writer), writer
    assert "chmod -R go-rwx /run/vexa/*" in ENTRYPOINT[first:ENTRYPOINT.index("/usr/local/bin/provision-key.sh")]
    assert "mkdir -p -m 0700 /run/vexa\n" not in ENTRYPOINT


def test_the_self_host_keys_are_written_root_only_over_an_earlier_file(tmp_path):
    """provision-key.sh writes the minted keys under umask 077 and renames them over any key.env a
    previous boot left (which may be world-readable): the result is 0600 whatever was there."""
    script = (LITE / "bin" / "provision-key.sh").read_text()
    body = script.split('if [ -n "$TOKS" ]; then\n', 1)[1].split("    supervisorctl", 1)[0]
    assert "/run/vexa/key.env" in body
    body = body.replace("/run/vexa", str(tmp_path))
    old = tmp_path / "key.env"
    old.write_text("VEXA_API_KEY=old\n")
    old.chmod(0o644)
    subprocess.run(["bash", "-c", body], env={"PATH": "/usr/bin:/bin", "TOKS": "VEXA_API_KEY=new"},
                   check=True)
    assert old.read_text() == "VEXA_API_KEY=new\n"
    assert (old.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "key.env.new").exists()


def _bot_display():
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("bot_display", str(LITE / "bin" / "bot-display"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_each_bot_starts_its_own_display_and_no_shared_one_exists():
    """No Xvfb under supervisord: the bot launcher starts one as the bot's uid, on a display number
    Xvfb picks itself, access control on, the cookie in the bot's HOME, no TCP; it checks the display's
    sockets are that Xvfb's before the bot runs, and a dead display takes the bot's group with it."""
    assert "[program:xvfb]" not in SUPERVISORD and "[program:fluxbox]" not in SUPERVISORD
    assert "Xvfb" not in SUPERVISORD.split("[group:vexa]")[1] and ":99" not in SUPERVISORD
    (xvfb,) = re.findall(r"^\s*Xvfb (.*)$", BOT_LAUNCH, flags=re.M)
    args = xvfb.split()
    assert "-displayfd" in args and "-nolisten" in args and args[args.index("-nolisten") + 1] == "tcp"
    assert "-ac" not in args and '-auth "$XAUTHORITY"' in xvfb and not re.search(r"\s:\d", xvfb)
    assert 'x11_dir="$home/x11"' in BOT_LAUNCH and 'mkdir -p -m 0700 "$x11_dir"' in BOT_LAUNCH
    assert 'export XAUTHORITY="$x11_dir/Xauthority"' in BOT_LAUNCH
    assert '/usr/local/bin/bot-display cookie "$XAUTHORITY" || exit 1' in BOT_LAUNCH
    check = '/usr/local/bin/bot-display check "$display" "$(cat "$x11_dir/xvfb.pid")" || exit 1'
    assert check in BOT_LAUNCH
    assert BOT_LAUNCH.index("Xvfb ") < BOT_LAUNCH.index(check) < BOT_LAUNCH.index('export DISPLAY=":$display"')
    assert BOT_LAUNCH.index('export DISPLAY=":$display"') < BOT_LAUNCH.index("exec node")
    assert 'wait "$!"\n    kill -KILL 0' in BOT_LAUNCH
    assert "/run/vexa/display" not in BOT_LAUNCH and ":99" not in BOT_LAUNCH
    assert "/run/vexa/display" not in ENTRYPOINT and "vexa-display" not in DOCKERFILE + ENTRYPOINT
    assert "rm -rf /tmp/.X11-unix /tmp/.X*-lock\nmkdir -m 1777 /tmp/.X11-unix" in ENTRYPOINT
    profiles = (ROOT / "core" / "runtime" / "src" / "runtime_kernel" / "profiles.py").read_text()
    assert "vexa-display" not in profiles
    smoke = (LITE / "tests" / "concurrent-bots.sh").read_text()
    assert 'python3 - < "$(dirname "$0")/bot_displays.py" || die' in smoke


def test_a_bots_cookie_is_its_own_and_fits_any_display_number(tmp_path):
    mod = _bot_display()
    home = tmp_path / "x11"
    home.mkdir(mode=0o700)
    path = home / "Xauthority"
    assert mod.main(["bot-display", "cookie", str(path)]) == 0
    data = path.read_bytes()
    assert (path.stat().st_mode & 0o777) == 0o600
    # one entry: family wild, empty address, EMPTY display number (so any), the cookie's name, 16 bytes
    assert data[:2] == b"\xff\xff" and data[2:6] == b"\0\0\0\0"
    assert data[6:8] == b"\0\x12" and data[8:26] == b"MIT-MAGIC-COOKIE-1" and data[26:28] == b"\0\x10"
    assert len(data) == 44
    first = data
    mod.main(["bot-display", "cookie", str(path)])
    assert path.read_bytes() != first                      # a new cookie every start


def test_a_bots_cookie_goes_only_into_its_own_closed_directory(tmp_path):
    import pytest

    mod = _bot_display()
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o755)
    assert mod.main(["bot-display", "cookie", str(loose / "Xauthority")]) == 1
    assert not (loose / "Xauthority").exists()
    with pytest.raises(FileNotFoundError):                 # no parent is made with a default mode
        mod.main(["bot-display", "cookie", str(tmp_path / "missing" / "Xauthority")])
    assert not (tmp_path / "missing").exists()


def _listen(address: str):
    import socket

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(address)
    s.listen(4)
    return s


def test_the_display_check_takes_only_the_bots_own_x_server(tmp_path):
    """Both sockets answered by this process (standing in for the bot's Xvfb): accepted. A wrong pid,
    a socket directory not owned as required, no abstract socket, or an abstract socket another
    process took first: refused."""
    import os
    import sys

    import pytest

    if not sys.platform.startswith("linux"):
        pytest.skip("abstract sockets and SO_PEERCRED are Linux's")
    mod = _bot_display()
    sock_dir = tmp_path / "x11-unix"
    sock_dir.mkdir()
    sock_dir.chmod(0o1777)
    me, n, pid = os.getuid(), "7", str(os.getpid())
    path = str(sock_dir / f"X{n}")
    path_sock = _listen(path)
    try:
        assert mod.check(n, pid, str(sock_dir), me) == 1                 # no abstract socket
        abstract = _listen("\0" + path)
        assert mod.check(n, pid, str(sock_dir), me) == 0
        assert mod.check(n, str(os.getpid() + 1), str(sock_dir), me) == 1
        assert mod.check(n, pid, str(sock_dir), me + 1) == 1             # directory not the owner's
        abstract.close()
        ready_r, ready_w = os.pipe()
        done_r, done_w = os.pipe()
        child = os.fork()
        if child == 0:                                 # another process takes the abstract name
            squat = _listen("\0" + path)
            os.write(ready_w, b"1")
            os.read(done_r, 1)
            squat.close()
            os._exit(0)
        os.read(ready_r, 1)
        try:
            assert mod.peer("\0" + path)[0] == child
            assert mod.check(n, pid, str(sock_dir), me) == 1
        finally:
            os.write(done_w, b"1")
            os.waitpid(child, 0)
    finally:
        path_sock.close()


def test_there_is_no_vnc_view():
    """No shared screen, so nothing to view: no x11vnc or noVNC program, package or switch. The image
    has no x11vnc at all, so the join module's escalation view never starts in Lite."""
    for name in ("x11vnc", "websockify", "VEXA_LITE_VNC"):
        assert name not in SUPERVISORD, name
    packages = DOCKERFILE.split("apt-get install", 1)[1].split("apt-get clean", 1)[0]
    for name in ("x11vnc", "novnc", "websockify"):
        assert name not in packages, name
    assert "VEXA_LITE_VNC" not in ENTRYPOINT and "/run/vexa/vnc" not in ENTRYPOINT
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


def test_the_container_profile_is_dockers_default_plus_user_namespaces():
    """seccomp-userns.json (the runtime's; the same file bot containers and Pods run under): Docker's default profile (deny by default; clone3 answered ENOSYS; namespaces
    behind CAP_SYS_ADMIN) with one rule more — clone and unshare for a process without that
    capability, so a meeting bot's Chromium can build its sandbox — and `make up` runs Lite under it."""
    import json

    profile = json.loads((ROOT / "core/runtime/src/runtime_kernel/seccomp-userns.json").read_text())
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    ours = [r for r in profile["syscalls"] if r.get("comment", "").startswith("Vexa:")]
    assert len(ours) == 1
    (rule,) = ours
    assert sorted(rule["names"]) == ["chroot", "clone", "unshare"] and rule["action"] == "SCMP_ACT_ALLOW"
    assert rule["excludes"] == {"caps": ["CAP_SYS_ADMIN"]} and "args" not in rule
    clone3 = [r for r in profile["syscalls"] if r["names"] == ["clone3"]]
    assert clone3 and clone3[0]["action"] == "SCMP_ACT_ERRNO" and clone3[0]["errnoRet"] == 38
    others = [r for r in profile["syscalls"] if r is not rule and "unshare" in r["names"]]
    assert others and all(r.get("includes", {}).get("caps") == ["CAP_SYS_ADMIN"] for r in others)
    makefile = (LITE / "Makefile").read_text()
    assert '--security-opt seccomp="$(ROOT)/core/runtime/src/runtime_kernel/seccomp-userns.json"' in makefile


def test_every_service_but_the_runtime_is_refused_user_namespaces():
    commands, program = {}, None
    for line in SUPERVISORD.splitlines():
        if line.startswith("["):
            program = line[len("[program:"):-1] if line.startswith("[program:") else None
        elif program and line.startswith("command="):
            commands[program] = line[len("command="):]
    assert set(commands) >= {"redis", "admin-api", "runtime", "agent-api", "meeting-api", "gateway", "mcp", "terminal"}
    for name, command in commands.items():
        wrapped = command.startswith("/usr/local/bin/no-user-namespaces ")
        assert wrapped == (name != "runtime"), name
    wrapper = (LITE / "bin" / "no-user-namespaces").read_text()
    assert 'USERNS = "/app/runtime/src/runtime_kernel/userns.py"' in wrapper
    assert "COPY core/runtime/src /app/runtime/src" in DOCKERFILE and "bin/no-user-namespaces" in DOCKERFILE
    assert wrapper.index("refuse_user_namespaces()") < wrapper.index("os.execvp")
    profiles = (ROOT / "core" / "runtime" / "src" / "runtime_kernel" / "profiles.py").read_text()
    assert profiles.count("user_namespaces=True") == 1


def test_the_bots_browser_checks_run_with_the_concurrent_bots():
    smoke = (LITE / "tests" / "concurrent-bots.sh").read_text()
    assert 'python3 - < "$(dirname "$0")/bot_browsers.py" || die' in smoke
