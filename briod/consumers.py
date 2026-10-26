"""Who has the camera open?  inotify IN_OPEN/IN_CLOSE on /dev/videoN plus a
/proc/*/fd scan to name the processes."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import struct
from dataclasses import dataclass, field

IN_OPEN = 0x00000020
IN_CLOSE_WRITE = 0x00000008
IN_CLOSE_NOWRITE = 0x00000010
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class Inotify:
    def __init__(self):
        self.fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.wds: dict[int, str] = {}

    def add(self, path: str, mask=IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE) -> int:
        wd = _libc.inotify_add_watch(self.fd, path.encode(), mask)
        if wd < 0:
            raise OSError(ctypes.get_errno(), f"inotify_add_watch {path}")
        self.wds[wd] = path
        return wd

    def remove(self, path: str):
        for wd, p in list(self.wds.items()):
            if p == path:
                _libc.inotify_rm_watch(self.fd, wd)
                del self.wds[wd]

    def read(self) -> list[tuple[str, int]]:
        try:
            buf = os.read(self.fd, 4096)
        except BlockingIOError:
            return []
        out, i = [], 0
        while i + 16 <= len(buf):
            wd, mask, _cookie, ln = struct.unpack_from("iIII", buf, i)
            i += 16 + ln
            if wd in self.wds:
                out.append((self.wds[wd], mask))
        return out

    def close(self):
        os.close(self.fd)


@dataclass
class Consumer:
    pid: int
    comm: str
    exe: str
    app_id: str
    ancestors: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.app_id or self.comm or os.path.basename(self.exe) or str(self.pid)

    def identifiers(self) -> list[str]:
        ids = [self.comm, os.path.basename(self.exe), self.exe, self.app_id] + self.ancestors
        return list(dict.fromkeys(i for i in ids if i))


_SCOPE_RE = re.compile(r"app-(?:flatpak-)?(?:[a-z]+-)?([A-Za-z0-9_.\-]+?)(?:-[0-9]+)?(?:@[0-9a-f]+)?\.(?:scope|service)")
_FLATPAK_RE = re.compile(r"app-flatpak-([A-Za-z0-9_.\-]+?)-[0-9]+\.scope")


def _read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode(errors="replace")
    except OSError:
        return ""


def app_id_for(pid: int) -> str:
    """Flatpak app id or systemd app scope name from the cgroup path."""
    cg = _read(f"/proc/{pid}/cgroup")
    m = _FLATPAK_RE.search(cg)
    if m:
        return m.group(1)
    m = _SCOPE_RE.search(cg)
    return m.group(1) if m else ""


def describe(pid: int) -> Consumer:
    comm = _read(f"/proc/{pid}/comm").strip()
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        exe = ""
    ancestors = []
    ppid = pid
    for _ in range(3):
        stat = _read(f"/proc/{ppid}/stat")
        try:
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            break
        if ppid <= 1:
            break
        c = _read(f"/proc/{ppid}/comm").strip()
        if c in ("systemd", "bash", "zsh", "fish", "sh"):
            break
        ancestors.append(c)
    return Consumer(pid, comm, exe, app_id_for(pid), ancestors)


def holders(nodes, exclude=()) -> list[Consumer]:
    """Processes (readable by us) holding any of `nodes` open."""
    targets = set(nodes)
    found = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in exclude:
            continue
        try:
            fds = os.scandir(f"/proc/{pid}/fd")
        except OSError:
            continue
        with fds:
            for fd in fds:
                try:
                    if os.readlink(fd.path) in targets:
                        found.append(describe(pid))
                        break
                except OSError:
                    continue
    return found
