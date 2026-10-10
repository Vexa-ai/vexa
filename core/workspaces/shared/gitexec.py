"""gitexec.py — the one way this domain runs git.

agent-api and the worker run git inside workspace repositories, and the files of a workspace —
its ``.git/`` included — are written by more than one party. Git trusts its repository: the
repository's own configuration and hooks can name programs that ordinary commands (``add``,
``commit``, ``status``, ``diff``, ``checkout``, ``push``) then run. A process holding platform
credentials must only ever run the programs it chose, so every git subprocess goes through
:func:`run_git`, and nothing else starts ``git`` (``core/agent/tests/test_git_single_path.py``
fails the build on a raw call in ``core/agent`` or ``core/workspaces``).

What :func:`run_git` pins, in the order git resolves it:

1. **Command-line configuration** — git's highest-precedence scope — fixes every setting that names
   a program by a fixed key: hooks come from an empty directory this process created
   (``core.hooksPath``), ``core.fsmonitor`` is off, the pager, editor, askpass and ssh command are
   fixed, the credential-helper list is emptied, signing is off and every signing program pinned,
   auto-maintenance and submodule recursion are off, global attribute and exclude files are
   ``/dev/null``, and no transport is open unless the caller opens one (``protocol.allow=never``;
   a ``GIT_ALLOW_PROTOCOL`` the caller passes takes precedence, which is how a network operation
   names the transports its own URL needs).
2. **The environment** is this process's minus everything that redirects git or names a program
   for it (repository discovery, injected configuration, external diff, pager, editor, exec path,
   templates) — the transport's own ``GIT_SSH_COMMAND``/askpass stay, as the operator or the caller
   set them; a repository cannot set an environment variable. Then:
   no system configuration (``GIT_CONFIG_NOSYSTEM``), a global configuration file and ``HOME`` this
   process owns (carrying only the system's ``safe.directory`` entries, so ownership rules are
   unchanged, plus ``safe.bareRepository=explicit``), no system attributes, no prompts. At a
   repository root ``GIT_WORK_TREE`` is the root itself, which outranks a ``core.worktree`` or
   ``core.bare`` the repository declares; ``GIT_CEILING_DIRECTORIES`` stops discovery from walking up
   into a parent repository.
3. **The repository's own configuration** cannot be skipped — git always reads ``$GIT_DIR/config``
   — and part of it cannot be overridden by name: filter, diff and merge drivers are keyed by a name
   the repository chooses, ``include``/``includeIf`` pull in other files, and ``extensions.*``,
   ``core.worktree`` and ``core.bare`` are read before command-line configuration. So before git
   runs, that file is reduced to an allow-list of plain settings (format, remotes' ``url`` and
   ``fetch``, branch tracking, identity); every other key is removed and logged by name.
4. **The repository's shape** is checked first: ``.git`` must be a real directory (not a link, not a
   ``gitdir:`` file) owned by this process or by the owner of the work tree, with no ``commondir``
   redirect and no object alternates, and its ``config`` a regular file. Git's own ownership rule
   applies as well (``.git`` and the work tree owned by this user, or the work tree listed in the
   system's ``safe.directory``), checked here because an explicit ``GIT_DIR`` (5) skips git's. A
   repository that fails is refused — exit status 128 with the reason, exactly as git reports its
   own refusals — never repaired.
5. **Git reads exactly the directory that was checked.** ``.git`` is opened once, the checks and
   the reduction in 3 and 4 run on that open directory, and git is given that same directory as
   ``GIT_DIR`` (``/proc/self/fd/N`` on Linux, the descriptor passed to git). Renaming ``.git`` or
   swapping another directory in after the check changes nothing git reads. Where ``/proc`` cannot
   name a descriptor (not Linux), ``GIT_DIR`` is the path and the directory's identity is
   re-checked immediately before and after git runs; any change is a refusal. A command run at a
   root with no ``.git`` gets a ``GIT_DIR`` that cannot exist, so a ``.git`` appearing after the
   check is never discovered. Attributes in the work tree can still name a filter, diff or merge
   driver, but a driver runs only when configuration defines its command, and no scope git reads
   can: the command line and global file are this module's, the system scope is off, and the
   repository's file is the reduced one in the directory git was handed.
6. ``diff``, ``show``, ``log`` and ``whatchanged`` run with ``--no-ext-diff --no-textconv``.

The canonical copy is ``core/agent/shared/gitexec.py``. It is vendored VERBATIM where a package
cannot import it: ``core/agent/llm/gitexec.py`` (the worker's ``llm`` package imports no product
code) and ``core/workspaces/shared/gitexec.py`` (the portable workspace primitives). The parity
fact ``git-exec`` in ``scripts/parity.json`` holds the copies byte-identical. Standard library only.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from typing import Mapping, Optional, Sequence

_log = logging.getLogger("gitexec")

#: Exit status of a refusal — the status git itself uses for a fatal setup error.
REFUSED = 128

# ── what the environment may not carry into git ────────────────────────────────────────────────
#: Inherited variables dropped before git starts: they redirect the repository, inject
#: configuration, or name a program git would run.
_DROP_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_OPTS", "GIT_PAGER", "PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "EDITOR", "VISUAL",
    "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_ATTR_SOURCE", "GIT_ATTR_NOSYSTEM", "GIT_ALLOW_PROTOCOL",
    "GIT_PROTOCOL_FROM_USER", "GIT_REPLACE_REF_BASE", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE",
    "HOME", "XDG_CONFIG_HOME",
})
_DROP_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")

#: Variables a caller may NOT set through ``env=``: this module owns them.
_OWNED_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_EXTERNAL_DIFF", "GIT_PAGER", "PAGER",
    "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_ATTR_NOSYSTEM",
    "GIT_ATTR_SOURCE", "HOME", "XDG_CONFIG_HOME",
})

#: The only ``-c`` keys a caller may add (lower-cased). Everything else is this module's.
_CALLER_CONFIG = frozenset({"user.name", "user.email"})

# ── the repository's own config: what may stay ─────────────────────────────────────────────────
_LOCAL_ALLOWED = re.compile(
    r"\A(?:"
    r"core\.(?:repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)"
    r"|extensions\.(?:objectformat|refstorage)"
    r"|remote\..+\.(?:url|fetch)"
    r"|branch\..+\.(?:remote|merge)"
    r"|user\.(?:name|email)"
    r")\Z",
    re.DOTALL,
)

#: Subcommands that render diffs: never through a program the repository names.
_DIFF_COMMANDS = frozenset({"diff", "show", "log", "whatchanged"})
#: Subcommands that make or copy a repository rather than operate in one.
_CREATING_COMMANDS = frozenset({"init", "clone"})
#: Global options a caller may not pass — the repository is ``cwd``, and only ``cwd``.
_FORBIDDEN_GLOBAL = ("-C", "--git-dir", "--work-tree", "--exec-path", "--namespace", "--bare",
                     "--config-env", "--super-prefix", "--attr-source", "-p", "--paginate")


class GitRefused(subprocess.CalledProcessError):
    """Raised (with ``check=True``) when the repository at ``cwd`` is refused before git runs."""


_state_lock = threading.Lock()
_private_dir: Optional[str] = None
_safe_directories: list[str] = []
_validated: dict[tuple, bool] = {}
_VALIDATED_MAX = 4096

#: How ``.git`` is held open between the check and git's run: a path-only handle on Linux (no read
#: needed, cannot be used to read or write by itself), never following a link.
_DIR_FLAGS = (getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
              | getattr(os, "O_CLOEXEC", 0))
#: The name, inside a directory only this process can write, given to git as ``GIT_DIR`` when the
#: work tree had no ``.git`` at the check: it never exists, so git finds no repository.
_NO_REPOSITORY = "no-repository"


def _carried_safe_directories() -> list[str]:
    """The system configuration's ``safe.directory`` entries — the image's ownership rule, kept so
    turning the system scope off changes nothing about which repositories git accepts."""
    env = {k: v for k, v in os.environ.items()
           if k not in _DROP_ENV and not k.startswith(_DROP_ENV_PREFIXES)}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        proc = subprocess.run(["git", "config", "--system", "--get-all", "safe.directory"],
                              cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()] if proc.returncode == 0 else []


def _make_private_dir() -> str:
    global _safe_directories
    path = tempfile.mkdtemp(prefix="vexa-gitexec-")          # 0700, ours, unpredictable name
    os.mkdir(os.path.join(path, "hooks"), 0o700)            # deliberately empty
    lines = ["[safe]", "\tbareRepository = explicit"]
    _safe_directories = _carried_safe_directories()
    for entry in _safe_directories:
        escaped = entry.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'\tdirectory = "{escaped}"')
    fd = os.open(os.path.join(path, "gitconfig"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _intact(path: str) -> bool:
    try:
        st = os.lstat(path)
        hooks = os.lstat(os.path.join(path, "hooks"))
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) & 0o077 == 0 and stat.S_ISDIR(hooks.st_mode)
            and hooks.st_uid == os.geteuid() and not os.listdir(os.path.join(path, "hooks")))


def private_dir() -> str:
    """The directory this process owns for git: ``HOME``, the global config, the empty hooks dir.
    Re-created if it disappeared or stopped being exclusively ours."""
    global _private_dir
    with _state_lock:
        if _private_dir is None or not _intact(_private_dir):
            _private_dir = _make_private_dir()
        return _private_dir


def _pinned_config(home: str) -> list[str]:
    return [
        f"core.hooksPath={os.path.join(home, 'hooks')}",
        "core.fsmonitor=false",
        "core.pager=cat",
        "core.editor=:",
        "sequence.editor=:",
        "core.askPass=",
        "core.sshCommand=ssh",
        "credential.helper=",
        f"core.attributesFile={os.devnull}",
        f"core.excludesFile={os.devnull}",
        "commit.gpgSign=false",
        "tag.gpgSign=false",
        "push.gpgSign=false",
        "log.showSignature=false",
        "gpg.program=false",
        "gpg.openpgp.program=false",
        "gpg.x509.program=false",
        "gpg.ssh.program=false",
        "protocol.allow=never",
        "submodule.recurse=false",
        "fetch.recurseSubmodules=false",
        "maintenance.auto=false",
        "gc.auto=0",
    ]


def _base_env(home: str, extra: Optional[Mapping[str, str]]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in _DROP_ENV and not k.startswith(_DROP_ENV_PREFIXES)}
    for key, value in (extra or {}).items():
        if key in _OWNED_ENV or key.startswith(_DROP_ENV_PREFIXES):
            raise ValueError(f"run_git: {key} is set by gitexec, not by a caller")
        env[key] = value
    env.update({
        "HOME": home,
        "XDG_CONFIG_HOME": home,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.path.join(home, "gitconfig"),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
        "GIT_EDITOR": ":",
        "GIT_SEQUENCE_EDITOR": ":",
    })
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _split_args(args: Sequence[str]) -> tuple[list[str], str, list[str]]:
    """``-c k=v`` pairs the caller put first, the subcommand, and the rest."""
    caller_config: list[str] = []
    rest = list(args)
    while rest and rest[0] == "-c":
        if len(rest) < 2:
            raise ValueError("run_git: -c needs a key=value")
        pair = rest[1]
        key = pair.split("=", 1)[0].strip().lower()
        if key not in _CALLER_CONFIG:
            raise ValueError(f"run_git: -c {key} is not a caller setting")
        caller_config.append(pair)
        rest = rest[2:]
    if not rest:
        raise ValueError("run_git: no git subcommand")
    sub = rest[0]
    if sub.startswith("-"):
        if sub in _FORBIDDEN_GLOBAL or any(sub.startswith(f + "=") for f in _FORBIDDEN_GLOBAL):
            raise ValueError(f"run_git: {sub} is not allowed; the repository is cwd")
        raise ValueError(f"run_git: unexpected global option {sub}")
    return caller_config, sub, rest[1:]


def _list_config(path: str, home: str,
                 fds: Sequence[int] = ()) -> Optional[list[tuple[str, Optional[str]]]]:
    """``(key, value)`` pairs of one config file, includes NOT followed; None if unreadable.
    ``fds``: descriptors ``path`` names through ``/proc/self/fd`` (passed to git at that number)."""
    proc = subprocess.run(["git", "config", "--file", path, "--list", "--null"],
                          cwd=home, env=_base_env(home, None), capture_output=True, timeout=30,
                          pass_fds=tuple(fds))
    if proc.returncode != 0:
        return None
    pairs: list[tuple[str, Optional[str]]] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", "surrogateescape")
        key, sep, value = text.partition("\n")
        pairs.append((key, value if sep else None))
    return pairs


def _rewrite_config(gitdir: str, path: str, keep: list[tuple[str, Optional[str]]], home: str,
                    mode: int, fds: Sequence[int] = ()) -> None:
    fd, tmp = tempfile.mkstemp(prefix="config.vexa-", dir=gitdir)
    os.close(fd)
    try:
        env = _base_env(home, None)
        for key, value in keep:
            subprocess.run(["git", "config", "--file", tmp, "--add", key,
                            "true" if value is None else value],
                           cwd=home, env=env, capture_output=True, check=True, timeout=30,
                           pass_fds=tuple(fds))
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Held:
    """``.git`` held open from the check until git has run. ``ref`` is the path this process and
    git use for it: ``/proc/self/fd/N`` when pinned, else the path itself (re-checked by
    identity around the run)."""

    __slots__ = ("fd", "ident", "ref", "pinned")

    def __init__(self, fd: int, ident: tuple[int, int], ref: str, pinned: bool) -> None:
        self.fd, self.ident, self.ref, self.pinned = fd, ident, ref, pinned

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def _pin(fd: int) -> Optional[str]:
    """``/proc/self/fd/N`` when it names exactly the directory ``fd`` holds (Linux), else None."""
    if not sys.platform.startswith("linux"):
        return None
    ref = f"/proc/self/fd/{fd}"
    try:
        named, held = os.stat(ref), os.fstat(fd)
    except OSError:
        return None
    return ref if (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino) else None


def _ours(uid: int) -> bool:
    """Git's rule for "owned by the current user", without its ``SUDO_UID`` allowance for root."""
    return uid == os.geteuid()


def _safe_directory(root: str) -> bool:
    """Git's ``safe.directory`` match of the work tree against the system entries this module
    carries (the same list the private global config holds)."""
    path = os.path.realpath(root)
    for entry in _safe_directories:
        if entry == "*":
            return True
        if entry.endswith("/*") and len(entry) >= 3:
            prefix = os.path.realpath(entry[:-2]) + os.sep
            if path.startswith(prefix):
                return True
        elif os.path.realpath(entry) == path:
            return True
    return False


def _same(root: str, held: _Held) -> bool:
    """``root/.git`` still names the directory that was checked."""
    try:
        st = os.lstat(os.path.join(root, ".git"))
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and (st.st_dev, st.st_ino) == held.ident


def _check_repository(root: str, home: str) -> "tuple[Optional[_Held], Optional[str]]":
    """``(held, None)`` when the repository at ``root`` may be used — its ``.git`` held open and its
    config reduced to the allow-list first — else ``(None, reason)``. ``(None, None)``: no ``.git``
    at ``root``; git, given a ``GIT_DIR`` that cannot exist, finds no repository there."""
    gitdir = os.path.join(root, ".git")
    try:
        st = os.lstat(gitdir)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot inspect .git ({exc.strerror})"
    if stat.S_ISLNK(st.st_mode):
        return None, ".git is a symbolic link"
    if not stat.S_ISDIR(st.st_mode):
        return None, ".git is not a directory"
    try:
        owner_of_tree = os.lstat(root).st_uid
    except OSError as exc:
        return None, f"cannot inspect the work tree ({exc.strerror})"
    if st.st_uid not in (os.geteuid(), owner_of_tree):
        return None, ".git is owned by neither this process nor the owner of the work tree"
    if not (_ours(st.st_uid) and _ours(owner_of_tree)) and not _safe_directory(root):
        return None, ("dubious ownership: .git or the work tree is not owned by this user and the "
                      "work tree is not a safe.directory")
    try:
        fd = os.open(gitdir, _DIR_FLAGS)
    except OSError as exc:
        return None, f"cannot open .git ({exc.strerror})"
    try:
        held_st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        return None, f"cannot inspect .git ({exc.strerror})"
    if not stat.S_ISDIR(held_st.st_mode) or (held_st.st_dev, held_st.st_ino) != (st.st_dev, st.st_ino):
        os.close(fd)
        return None, ".git changed while it was being checked"
    ref = _pin(fd)
    held = _Held(fd, (st.st_dev, st.st_ino), ref or gitdir, ref is not None)
    reason = _check_contents(held, home, gitdir)
    if reason is not None:
        held.close()
        return None, reason
    return held, None


def _check_contents(held: _Held, home: str, gitdir: str) -> Optional[str]:
    """The checks and the reduction inside the held ``.git``, all through ``held.ref`` (``gitdir``
    is its path, for the log only)."""
    fds = (held.fd,) if held.pinned else ()
    for redirect in ("commondir", os.path.join("objects", "info", "alternates")):
        if os.path.lexists(os.path.join(held.ref, redirect)):
            return f".git/{redirect} redirects the repository"
    config = os.path.join(held.ref, "config")
    try:
        cst = os.lstat(config)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot inspect .git/config ({exc.strerror})"
    if not stat.S_ISREG(cst.st_mode):
        return ".git/config is not a regular file"
    stamp = (*held.ident, cst.st_dev, cst.st_ino, cst.st_size, cst.st_mtime_ns, cst.st_ctime_ns)
    if _validated.get(stamp):
        return None
    pairs = _list_config(config, home, fds)
    if pairs is None:
        return ".git/config cannot be read"
    keep = [(k, v) for k, v in pairs if _LOCAL_ALLOWED.match(k)]
    if len(keep) != len(pairs):
        removed = sorted({k for k, _ in pairs if not _LOCAL_ALLOWED.match(k)})
        _log.warning("gitexec: removed %d setting(s) from %s that git would act on: %s",
                     len(pairs) - len(keep), os.path.join(gitdir, "config"), ", ".join(removed))
        try:
            _rewrite_config(held.ref, config, keep, home, stat.S_IMODE(cst.st_mode) & 0o644, fds)
        except (OSError, subprocess.SubprocessError) as exc:
            return f".git/config could not be reduced ({exc})"
        try:
            cst = os.lstat(config)
        except OSError:
            return None
        stamp = (*held.ident, cst.st_dev, cst.st_ino, cst.st_size, cst.st_mtime_ns, cst.st_ctime_ns)
    with _state_lock:
        if len(_validated) >= _VALIDATED_MAX:
            _validated.clear()
        _validated[stamp] = True
    return None


def _refused(argv: list[str], root: str, reason: str, *, check: bool, capture_output: bool,
             text: bool) -> subprocess.CompletedProcess:
    message = f"vexa: refusing to run git in {root}: {reason}\n"
    _log.warning(message.strip())
    empty: "str | bytes" = "" if text else b""
    err: "str | bytes" = message if text else message.encode()
    if check:
        raise GitRefused(REFUSED, argv, output=empty, stderr=err)
    return subprocess.CompletedProcess(argv, REFUSED, stdout=empty if capture_output else None,
                                       stderr=err if capture_output else None)


def run_git(cwd: "Optional[str | os.PathLike[str]]", *args: str,
            env: Optional[Mapping[str, str]] = None, check: bool = False,
            capture_output: bool = True, text: bool = True, timeout: Optional[float] = None,
            input: "Optional[str | bytes]" = None) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd`` (the repository root; None = this process's cwd).

    ``args`` start with the subcommand, optionally preceded by ``-c user.name=…`` /
    ``-c user.email=…``. ``env`` adds variables (identity, ``GIT_ALLOW_PROTOCOL``, ``GIT_ASKPASS``,
    ``GIT_SSH_COMMAND``) over the scrubbed inherited environment. Returns the
    :class:`subprocess.CompletedProcess`; with ``check`` a non-zero status raises
    :class:`subprocess.CalledProcessError` (:class:`GitRefused` for a refused repository)."""
    caller_config, sub, rest = _split_args(args)
    home = private_dir()
    root = os.path.abspath(os.fspath(cwd) if cwd is not None else os.getcwd())
    run_env = _base_env(home, env)
    run_env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.realpath(root)) or os.sep
    argv = ["git", "--no-pager"]
    for pair in caller_config:
        argv += ["-c", pair]
    for pair in _pinned_config(home):
        argv += ["-c", pair]
    argv.append(sub)
    if sub in _DIFF_COMMANDS:
        argv += ["--no-ext-diff", "--no-textconv"]
    argv += rest
    shape = {"check": check, "capture_output": capture_output, "text": text}

    held, reason = _check_repository(root, home)
    if reason is not None:
        return _refused(argv, root, reason, **shape)
    try:
        if sub in _CREATING_COMMANDS:
            # init/clone make a repository; they are not handed one (an existing .git was still
            # checked and reduced above, as before).
            return subprocess.run(argv, cwd=root, env=run_env, check=check,
                                  capture_output=capture_output, text=text, timeout=timeout,
                                  input=input)
        if held is None:
            run_env["GIT_DIR"] = os.path.join(home, _NO_REPOSITORY)
            fds: tuple[int, ...] = ()
        else:
            run_env["GIT_DIR"] = held.ref
            run_env["GIT_WORK_TREE"] = root
            fds = (held.fd,) if held.pinned else ()
            if not held.pinned and not _same(root, held):
                return _refused(argv, root, ".git changed after it was checked", **shape)
        proc = subprocess.run(argv, cwd=root, env=run_env, check=check,
                              capture_output=capture_output, text=text, timeout=timeout,
                              input=input, pass_fds=fds)
        if held is not None and not held.pinned and not _same(root, held):
            return _refused(argv, root, ".git changed while git ran", **shape)
        return proc
    finally:
        if held is not None:
            held.close()
