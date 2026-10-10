"""The user-namespace refusal every Lite child but a meeting bot runs under (runtime_kernel.userns).

The program is checked by running it — a small classic-BPF interpreter over the inputs that matter —
for both machines; then, on Linux where a process may create a user namespace at all (a container
under Lite's seccomp profile), the installed filter is shown to take that away from a process and
everything it starts, while fork, threads and every other namespace request keep working.
"""
from __future__ import annotations

import ctypes
import errno
import os
import sys
import threading

import pytest

from runtime_kernel import userns

ALLOW = userns.SECCOMP_RET_ALLOW
EPERM = userns.SECCOMP_RET_ERRNO | errno.EPERM
ENOSYS = userns.SECCOMP_RET_ERRNO | errno.ENOSYS
CLONE_NEWNS, CLONE_NEWPID, CLONE_NEWNET, SIGCHLD = 0x00020000, 0x20000000, 0x40000000, 17
I386, ARM = 0x40000003, 0x40000028


def run(rows, arch: int, nr: int, arg0: int = 0) -> int:
    """Classic BPF over a seccomp_data of (nr, arch, args[0])."""
    data = {0: nr, 4: arch, 16: arg0 & 0xFFFFFFFF, 20: arg0 >> 32}
    acc, pc = 0, 0
    while True:
        code, jt, jf, k = rows[pc]
        if code == userns._LD_W_ABS:
            acc = data[k]
            pc += 1
        elif code == userns._RET:
            return k
        else:
            hit = {userns._JEQ: acc == k, userns._JGE: acc >= k, userns._JSET: bool(acc & k)}[code]
            pc += 1 + (jt if hit else jf)
        assert 0 <= pc < len(rows)


@pytest.mark.parametrize("machine", sorted(userns.ARCHES))
def test_the_program_refuses_a_new_user_namespace_and_nothing_else(machine):
    rows = userns.program(machine)
    audit, clone, unshare, clone3, x32 = userns.ARCHES[machine]
    assert run(rows, audit, unshare, userns.CLONE_NEWUSER) == EPERM
    assert run(rows, audit, unshare, userns.CLONE_NEWUSER | CLONE_NEWNS) == EPERM
    assert run(rows, audit, clone, userns.CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNET) == EPERM
    assert run(rows, audit, clone3) == ENOSYS                   # the C library falls back to clone
    assert run(rows, audit, clone, SIGCHLD) == ALLOW            # fork
    assert run(rows, audit, clone, 0x3D0F00) == ALLOW           # a thread
    assert run(rows, audit, unshare, CLONE_NEWNS) == ALLOW      # not a user namespace: the kernel decides
    assert run(rows, audit, 0) == ALLOW and run(rows, audit, 1) == ALLOW
    other = I386 if machine == "x86_64" else ARM
    assert run(rows, other, 0) == EPERM                          # another ABI: refused outright
    if x32:
        assert run(rows, audit, userns.X32_SYSCALL_BIT | unshare, userns.CLONE_NEWUSER) == EPERM
        assert run(rows, audit, userns.X32_SYSCALL_BIT | 0) == EPERM


def test_a_machine_without_a_program_is_unsupported():
    with pytest.raises(userns.Unsupported):
        userns.program("riscv64")
    with pytest.raises(userns.Unsupported):
        userns.refusal("s390x")


def _in_child(fn) -> int:
    """Run ``fn`` in a forked child; its int result is the child's exit status."""
    pid = os.fork()
    if pid == 0:
        try:
            os._exit(fn())
        except BaseException:                    # noqa: BLE001 — any failure is a distinct status
            os._exit(99)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _unshare_user() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(userns.CLONE_NEWUSER) == 0:
        return 0
    return ctypes.get_errno()


def _filtered_then(fn):
    def run_it() -> int:
        userns.refuse_user_namespaces()
        return fn()
    return run_it


linux = pytest.mark.skipif(not sys.platform.startswith("linux") or userns.machine() not in userns.ARCHES,
                           reason="a seccomp filter is Linux's, for x86_64 and aarch64")


@linux
def test_the_installed_filter_takes_user_namespaces_away_for_good():
    if _in_child(_unshare_user) != 0:
        pytest.skip("this process may not create a user namespace anyway (Docker's default profile); "
                    "run under deploy/lite/seccomp.json to see the filter take it away")
    assert _in_child(_filtered_then(_unshare_user)) == errno.EPERM

    def grandchild() -> int:                     # inherited by everything the process starts
        return _in_child(_unshare_user)
    assert _in_child(_filtered_then(grandchild)) == errno.EPERM


@linux
def test_fork_threads_and_exec_keep_working_under_the_filter():
    def work() -> int:
        out = []
        t = threading.Thread(target=lambda: out.append(1))
        t.start()
        t.join()
        if out != [1] or _in_child(lambda: 0) != 0:
            return 1
        r = os.system("true")
        return 0 if r == 0 else 2
    assert _in_child(_filtered_then(work)) == 0
