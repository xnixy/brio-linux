"""Optional focused-window backends (Sway, Hyprland).

GNOME and KDE report focus through the D-Bus method FocusChanged(app_id),
called by the Shell extension / KWin script shipped in data/.
Each backend runs a reader thread and calls `callback(app_id)` on the GLib
main loop.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import socket
import struct
import threading

from gi.repository import GLib

log = logging.getLogger("briod.focus")


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def _hyprland_socket() -> str | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    pats = [f"{_runtime_dir()}/hypr/{sig}/.socket2.sock"] if sig else []
    pats.append(f"{_runtime_dir()}/hypr/*/.socket2.sock")
    for p in pats:
        hits = sorted(glob.glob(p), key=os.path.getmtime, reverse=True)
        if hits:
            return hits[0]
    return None


def _sway_socket() -> str | None:
    s = os.environ.get("SWAYSOCK")
    if s and os.path.exists(s):
        return s
    hits = sorted(glob.glob(f"{_runtime_dir()}/sway-ipc.*.sock"), key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def _hyprland_loop(path, emit, stop):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(path)
        f = s.makefile("r", encoding="utf-8", errors="replace")
        for line in f:
            if stop.is_set():
                return
            if line.startswith("activewindow>>"):
                cls = line[len("activewindow>>"):].split(",", 1)[0].strip()
                emit(cls)


def _sway_loop(path, emit, stop):
    magic = b"i3-ipc"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(path)
        payload = b'["window"]'
        s.sendall(magic + struct.pack("=II", len(payload), 2) + payload)

        def recv(n):
            buf = b""
            while len(buf) < n:
                chunk = s.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("sway closed")
                buf += chunk
            return buf

        while not stop.is_set():
            hdr = recv(14)
            ln, _typ = struct.unpack("=II", hdr[6:])
            body = json.loads(recv(ln) or b"{}")
            if isinstance(body, dict) and body.get("change") == "focus":
                c = body.get("container", {})
                app = c.get("app_id") or (c.get("window_properties") or {}).get("class") or ""
                emit(app)


class FocusWatcher:
    def __init__(self, callback):
        self.callback = callback
        self.stop = threading.Event()
        self.thread = None

    def start(self) -> str | None:
        """Start the first backend found; returns its name or None."""
        for name, find, loop in (("hyprland", _hyprland_socket, _hyprland_loop),
                                 ("sway", _sway_socket, _sway_loop)):
            path = find()
            if path:
                def run(path=path, loop=loop, name=name):
                    try:
                        loop(path, lambda app: GLib.idle_add(self.callback, app), self.stop)
                    except Exception as e:  # noqa: BLE001 - backend is best-effort
                        log.warning("%s focus backend stopped: %s", name, e)
                self.thread = threading.Thread(target=run, name=f"focus-{name}", daemon=True)
                self.thread.start()
                log.info("focus backend: %s (%s)", name, path)
                return name
        return None

    def close(self):
        self.stop.set()
