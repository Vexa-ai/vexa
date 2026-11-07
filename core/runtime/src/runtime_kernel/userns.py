"""userns.py — take away a process's ability to create user namespaces, for good.

Lite runs its container under a seccomp profile (seccomp-userns.json beside this file) that, unlike Docker's
default, lets an unprivileged process create a user namespace: Chromium's sandbox is built on one,
and a meeting bot's browser renders pages nobody here controls. Nothing else in the container needs
it, and inside a user namespace an ordinary process is "root" to a large part of the kernel. So every
other process loses it again, here: a seccomp filter, installed before the program runs and inherited
by everything it starts, that answers

* ``clone`` and ``unshare`` asking for ``CLONE_NEWUSER`` with EPERM;
* ``clone3`` with ENOSYS (its flags are behind a pointer a filter cannot read; the C library then
  falls back to ``clone``, the same answer Docker's default profile gives an unprivileged process);
* any system call made through another ABI than the native one (32-bit, x32) with EPERM.

Filters only add up, so a process that has this one cannot shed it. Installing it needs
``no_new_privs`` (set here first) or CAP_SYS_ADMIN. x86_64 and aarch64 only: elsewhere
:func:`refusal` raises, and the caller refuses to start the child.

The other half is the profile that grants it (``seccomp-userns.json`` beside this file: Docker
Engine's default profile plus one rule). Lite runs its container under it; the docker backend runs a
meeting bot's own container under it (:func:`container_profile`); on Kubernetes the chart installs it
on the nodes and bot Pods name it (``RUNTIME_K8S_BOT_SECCOMP_PROFILE``). Its licence is beside it.

Standard library only: the Lite service wrapper (deploy/lite/bin/no-user-namespaces) loads this file
on its own, outside the runtime's package.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import struct
from typing import Callable

#: The seccomp profile that lets a meeting bot's browser build its sandbox (see the module docstring).
PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seccomp-userns.json")

CLONE_NEWUSER = 0x10000000
X32_SYSCALL_BIT = 0x40000000
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2

SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000

_LD_W_ABS, _JEQ, _JGE, _JSET, _RET = 0x20, 0x15, 0x35, 0x45, 0x06
_NR, _ARCH, _ARG0_LOW = 0, 4, 16            # struct seccomp_data offsets (little-endian arg low word)

#: machine → (AUDIT_ARCH, clone, unshare, clone3, refuse x32-tagged numbers)
ARCHES = {
    "x86_64": (0xC000003E, 56, 272, 435, True),
    "aarch64": (0xC00000B7, 220, 97, 435, False),
}
_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


class Unsupported(OSError):
    """No filter for this machine."""


def machine() -> str:
    m = platform.machine().lower()
    return _ALIASES.get(m, m)


def program(arch: str) -> list[tuple[int, int, int, int]]:
    """The classic-BPF program, as (code, jt, jf, k) rows, for one machine."""
    if arch not in ARCHES:
        raise Unsupported(errno.ENOSYS, f"no user-namespace filter for {arch!r}")
    audit, nr_clone, nr_unshare, nr_clone3, x32 = ARCHES[arch]
    rows: list = []
    # labels resolved below: "allow", "eperm", "enosys", "flags"
    rows.append((_LD_W_ABS, 0, 0, _ARCH))
    rows.append((_JEQ, 0, "eperm", audit))                # another ABI: refused
    rows.append((_LD_W_ABS, 0, 0, _NR))
    if x32:
        rows.append((_JGE, "eperm", 0, X32_SYSCALL_BIT))  # x32 numbers: refused
    rows.append((_JEQ, "enosys", 0, nr_clone3))
    rows.append((_JEQ, "flags", 0, nr_unshare))
    rows.append((_JEQ, "flags", "allow", nr_clone))
    labels = {"flags": len(rows)}
    rows.append((_LD_W_ABS, 0, 0, _ARG0_LOW))
    rows.append((_JSET, "eperm", "allow", CLONE_NEWUSER))
    labels["allow"] = len(rows)
    rows.append((_RET, 0, 0, SECCOMP_RET_ALLOW))
    labels["eperm"] = len(rows)
    rows.append((_RET, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    labels["enosys"] = len(rows)
    rows.append((_RET, 0, 0, SECCOMP_RET_ERRNO | errno.ENOSYS))
    out = []
    for i, (code, jt, jf, k) in enumerate(rows):
        jt = labels[jt] - i - 1 if isinstance(jt, str) else jt
        jf = labels[jf] - i - 1 if isinstance(jf, str) else jf
        out.append((code, jt, jf, k))
    return out


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def refusal(arch: str | None = None) -> Callable[[], None]:
    """Resolved before a fork: the call that, in the (single-threaded) process making it, sets
    ``no_new_privs`` and installs the filter. Raises :class:`Unsupported` on a machine without one;
    the returned call raises OSError if the kernel refuses either step."""
    rows = program(arch or machine())
    blob = b"".join(struct.pack("<HBBI", *row) for row in rows)
    buf = ctypes.create_string_buffer(blob, len(blob))
    prog = _SockFprog(len(rows), ctypes.cast(buf, ctypes.c_void_p))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    except (OSError, AttributeError) as e:
        raise Unsupported(errno.ENOSYS, "prctl is not available here") from e
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int

    def _install() -> None:
        keep = (buf, prog)                                    # alive until the filter is in
        if prctl(PR_SET_NO_NEW_PRIVS, 1, None, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        if prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(keep[1]), 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "installing the user-namespace filter failed")
    return _install


def refuse_user_namespaces() -> None:
    """Install the filter in this process now (it must be single-threaded)."""
    refusal()()


def container_profile(path: str = PROFILE_PATH) -> str:
    """The profile as the Docker API takes it (``HostConfig.SecurityOpt`` ``seccomp=<json>``): the
    file's JSON, compact. Raises OSError/ValueError when the file is missing or not JSON."""
    with open(path, encoding="utf-8") as f:
        return json.dumps(json.load(f), separators=(",", ":"))
