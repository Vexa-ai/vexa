"""Run INSIDE a booted Lite container while two or more meeting bots run (tests/concurrent-bots.sh
pipes it in during its window): each bot's browser is sandboxed and carries none of the bot's own
environment, and only bots may create user namespaces.

* every Chromium process of every bot runs without --no-sandbox, and each renderer is in a PID
  namespace of its own (Chromium's namespace sandbox), not the bot's;
* no Chromium process's environment holds the bot's constructor (VEXA_BOT_CONFIG) or any key that is
  not on the browser's list (@vexa/remote-browser sandbox.ts);
* every process but the runtime and a meeting bot's tree runs under one seccomp filter more than the
  container's own (the user-namespace refusal: core/runtime userns.py), and a bot's own processes do
  not (its Chromium processes add Chromium's own filters).

Stdlib only; runs as root; never prints an environment value. Exits 1 naming what failed.
"""
import json
import os
import sys

WORKLOAD_UID_BASE = 1_500_000_000
BROWSER_ENV_KEYS = {
    "PATH", "HOME", "TMPDIR", "TZ", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "PULSE_SERVER", "PULSE_SINK", "PULSE_SOURCE",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}
# what Chromium sets in its own children's environment
CHROMIUM_OWN = {"CHROME_DESKTOP", "CHROME_WRAPPER", "GOOGLE_CRASHPAD_PIPE_FD", "PWD", "SHLVL", "_"}


def status(pid: int) -> dict:
    out = {}
    with open(f"/proc/{pid}/status", encoding="utf-8") as f:
        for line in f:
            key, _, value = line.partition(":")
            out[key] = value.split()
    return out


def cmdline(pid: int) -> list:
    """The process's arguments. Chromium rewrites its children's titles into one space-separated
    string, so a single argument holding spaces is split on them."""
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        args = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    return args[0].split() if len(args) == 1 and " " in args[0] else args


def environ_keys(pid: int) -> set:
    with open(f"/proc/{pid}/environ", "rb") as f:
        return {e.split(b"=", 1)[0].decode(errors="replace") for e in f.read().split(b"\0") if b"=" in e}


def environ_as(user: int, pids: list) -> dict:
    """{pid: [environment keys] or None}, read in a child that has become ``user``."""
    r, w = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(r)
        out = {}
        try:
            os.setgroups([])
            os.setgid(user)
            os.setuid(user)
            for pid in pids:
                try:
                    out[pid] = sorted(environ_keys(pid))
                except OSError:
                    out[pid] = None
        except OSError:
            pass
        os.write(w, json.dumps(out).encode())
        os._exit(0)
    os.close(w)
    data = b""
    while chunk := os.read(r, 65536):
        data += chunk
    os.close(r)
    os.waitpid(child, 0)
    return {int(k): v for k, v in json.loads(data or b"{}").items()}


def parent(pid: int) -> int:
    with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
        return int(f.read().rsplit(")", 1)[1].split()[1])


def main() -> int:
    failures = []
    procs = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            procs[pid] = (status(pid), cmdline(pid))
        except OSError:
            continue
    uid = lambda st: int(st["Uid"][0])                                  # noqa: E731
    filters = lambda st: int((st.get("Seccomp_filters") or ["-1"])[0])  # noqa: E731
    bots = {pid for pid, (st, line) in procs.items()
            if uid(st) >= WORKLOAD_UID_BASE and line[:2] == ["node", "dist/index.js"]}
    if len(bots) < 2:
        print(f"  ✗ bot browsers: {len(bots)} bot(s) running; two are needed", file=sys.stderr)
        return 1
    chromes = {pid: (st, line) for pid, (st, line) in procs.items()
               if uid(st) >= WORKLOAD_UID_BASE and line and "chrom" in os.path.basename(line[0]).lower()}
    renderers, browsers_read = 0, 0
    for pid, (st, line) in chromes.items():
        if "--no-sandbox" in line:
            failures.append(f"a bot's Chromium (pid {pid}) runs with --no-sandbox")
        if "--type=renderer" in line:
            renderers += 1
            # NSpid (world-readable) lists the process's pid in each PID namespace it is in, from the
            # container's down: a sandboxed renderer is also in one of its own.
            if len(st.get("NSpid", [])) < 2:
                failures.append(f"a bot's renderer (pid {pid}) is in no PID namespace of its own: no sandbox")
    if not renderers:
        failures.append("no bot's Chromium renderer was found")
    # The environment of each bot's main browser process, read as that bot (root here holds no
    # CAP_SYS_PTRACE, so it may not read another uid's process environment).
    for b in bots:
        bot_uid = uid(procs[b][0])
        mains = [pid for pid, (st, line) in chromes.items() if uid(st) == bot_uid
                 and os.path.basename(line[0]) in ("chrome", "chromium", "headless_shell")
                 and not any(a.startswith("--type=") for a in line)]
        for pid, keys in environ_as(bot_uid, mains).items():
            if keys is None:
                failures.append(f"bot {bot_uid}: its own browser's environment (pid {pid}) could not be read")
                continue
            browsers_read += 1
            extra = set(keys) - BROWSER_ENV_KEYS - CHROMIUM_OWN
            if "VEXA_BOT_CONFIG" in extra:
                failures.append(f"a bot's Chromium (pid {pid}) holds the bot's constructor")
            elif extra:
                failures.append(f"a bot's Chromium (pid {pid}) holds keys off the browser's list: {sorted(extra)}")
    if browsers_read < len(bots):
        failures.append(f"read {browsers_read} browser environments for {len(bots)} bots")
    runtime = next((pid for pid, (_st, line) in procs.items() if line[1:3] == ["-m", "runtime_kernel"]), None)
    base = filters(procs[1][0])
    if base < 0:
        print("  · bot browsers: this kernel does not report seccomp filter counts; filter check skipped")
    runtime_line = procs[runtime][1] if runtime else None
    for pid, (st, line) in procs.items():
        if base < 0 or pid == 1 or not line or line == runtime_line:
            continue                                   # supervisord, the runtime (and its unexec'd forks)
        in_bot = uid(st) >= WORKLOAD_UID_BASE
        if pid in chromes:
            continue                                   # Chromium's sandbox adds seccomp filters of its own
        want = base if in_bot else base + 1
        # processes started by docker exec (this check, an operator's shell) are not the container's own
        p, outside = pid, False
        try:
            while p > 1:
                p = parent(p)
                if p == 0:
                    outside = True
                    break
        except OSError:
            continue
        if outside or filters(st) == want:
            continue
        if in_bot:
            failures.append(f"a bot process ({st['Name'][0]}, pid {pid}) cannot create the user namespace its browser needs")
        else:
            failures.append(f"process {st['Name'][0]} (pid {pid}, uid {uid(st)}) may create user namespaces")
    if failures:
        for f in sorted(set(failures)):
            print(f"  ✗ bot browsers: {f}", file=sys.stderr)
        return 1
    print(f"  ✓ bot browsers: {len(bots)} bots, {len(chromes)} Chromium processes — none unsandboxed, "
          f"{renderers} renderers each in a PID namespace of its own, {browsers_read} browsers holding none of "
          "the bot's environment; "
          "user namespaces refused to every process but the runtime and the bots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
