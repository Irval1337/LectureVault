"""Unprivileged Linux Landlock launcher: a CLI can access only its own home/work/tmp.

No shell, setuid, Docker socket or privileged container. Fail closed when unsupported.
Linux UAPI: https://docs.kernel.org/userspace-api/landlock.html
"""
import ctypes
import os
from pathlib import Path
import platform
import resource
import sys

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1, 2, 4, 8
READ = EXECUTE | READ_FILE | READ_DIR
HANDLED = (1 << 15) - 1  # ABI 3, including REFER and TRUNCATE
WRITE = HANDLED & ~(EXECUTE | (1 << 6) | (1 << 11))  # No executable/private devices


class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int)]


def abi():
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.syscall(444, 0, 0, 1)


def restrict(home, work, temporary, read_file=None):
    if platform.machine() not in {'x86_64', 'aarch64'} or abi() < 3:
        raise RuntimeError('Linux Landlock ABI 3 or newer is required')
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.syscall(444, ctypes.byref(Ruleset(HANDLED)), ctypes.sizeof(Ruleset), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'landlock_create_ruleset')
    try:
        rules = [(p, READ) for p in ['/app', '/opt', '/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc', '/sys/devices/system/cpu']]
        rules += [(p, READ_FILE | WRITE_FILE) for p in ['/dev/null', '/dev/urandom', '/dev/random', '/dev/zero', '/dev/tty']]
        rules += [(p, READ_FILE) for p in ['/proc/meminfo', '/proc/cpuinfo', '/proc/stat', '/proc/sys/kernel/osrelease']]
        rules += [(str(p), WRITE) for p in [home, work, temporary]]
        if read_file is not None:
            rules.append((str(read_file), READ_FILE))
        for path, rights in rules:
            if not Path(path).exists():
                continue
            target = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                if not Path(path).is_dir():
                    rights &= EXECUTE | READ_FILE | WRITE_FILE | (1 << 14)
                rule = PathRule(rights, target)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                    raise OSError(ctypes.get_errno(), 'landlock_add_rule')
            finally:
                os.close(target)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, fd, 0) < 0:
            raise OSError(ctypes.get_errno(), 'landlock_restrict_self')
    finally:
        os.close(fd)


def main():
    if os.geteuid() == 0 or len(sys.argv) < 5:
        raise SystemExit('Non-root isolated CLI invocation required')
    home, work, temporary = map(Path, sys.argv[1:4])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    command = sys.argv[4:]
    read_file = None
    if command[0] == '--read-file':
        read_file = Path(command[1])
        command = command[2:]
    restrict(home, work, temporary, read_file)
    os.chdir(work)
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    main()
