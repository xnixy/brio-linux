"""Async D-Bus client for briod."""
from __future__ import annotations

import json

from gi.repository import Gio, GLib, GObject

BUS_NAME = "io.github.BrioControl.Daemon"
OBJECT_PATH = "/io/github/BrioControl/Daemon"
IFACE = "io.github.BrioControl1"


class Client(GObject.Object):
    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "availability": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, autostart=True):
        super().__init__()
        self.autostart = autostart
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.available = False
        self.state: dict = {}
        self.bus.signal_subscribe(BUS_NAME, IFACE, "StateChanged", OBJECT_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self._on_signal)
        Gio.bus_watch_name_on_connection(self.bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._appeared, self._vanished)

    # ------------------------------------------------------------ lifecycle
    def start_daemon(self):
        """D-Bus activation (data/io.github.BrioControl.service → briod.service)."""
        self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "StartServiceByName", GLib.Variant("(su)", (BUS_NAME, 0)), None,
                      Gio.DBusCallFlags.NONE, 5000, None, self._started, None)

    def _started(self, bus, res, _data):
        try:
            bus.call_finish(res)
        except GLib.Error as e:
            self.emit("error", f"Could not start briod: {e.message}")

    def _appeared(self, *_):
        self.available = True
        self.refresh()
        self.emit("availability", True)

    def _vanished(self, *_):
        if self.autostart:              # only on the first "not running" report
            self.autostart = False
            self.start_daemon()
        self.available = False
        self.state = {}
        self.emit("availability", False)

    def _on_signal(self, _conn, _sender, _path, _iface, _signal, params):
        self.refresh(params.unpack()[0])

    # ---------------------------------------------------------------- calls
    def refresh(self, reason="refresh"):
        def done(result):
            self.state = json.loads(result)
            self.emit("state-changed", reason)
        self.call("GetState", None, on_result=done)

    def call(self, method, signature=None, *args, on_result=None):
        params = GLib.Variant(signature, args) if signature else None

        def cb(bus, res, _):
            try:
                r = bus.call_finish(res)
            except GLib.Error as e:
                msg = Gio.DBusError.strip_remote_error(e) if Gio.DBusError.is_remote_error(e) else e.message
                self.emit("error", msg)
                return
            if on_result:
                vals = r.unpack() if r is not None else ()
                on_result(vals[0] if vals else None)

        self.bus.call(BUS_NAME, OBJECT_PATH, IFACE, method, params, None,
                      Gio.DBusCallFlags.NONE, 10000, None, cb, None)

    # ----------------------------------------------------------- helpers
    def set_control(self, slot, tab, key, value, profile=""):
        self.call("SetControl", "(sssss)", profile, slot, tab, key, json.dumps(value))

    def set_controls(self, slot, changes, profile=""):
        def done(errs):
            for e in json.loads(errs or "[]"):
                self.emit("error", e)
        self.call("SetControls", "(sss)", profile, slot, json.dumps(changes), on_result=done)

    def set_device_setting(self, slot, key, value):
        self.call("SetDeviceSetting", "(sss)", slot, key, json.dumps(value))

    def set_option(self, key, value):
        self.call("SetOption", "(ss)", key, json.dumps(value))

    def register_preview(self, slot, active):
        if self.available:
            self.call("RegisterPreview", "(sb)", slot, active)
