"""briod: hotplug-aware apply daemon exposing a session D-Bus API."""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import signal
import sys
import time

import gi

gi.require_version("GUdev", "1.0")
from gi.repository import Gio, GLib, GUdev  # noqa: E402

try:                                   # GLib ≥ 2.80
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix
    _signal_add = GLibUnix.signal_add
except (ValueError, ImportError):
    _signal_add = GLib.unix_signal_add

from . import consumers, uvc  # noqa: E402
from .engine import APPLY_ORDER, Camera, drift  # noqa: E402
from .models import brio  # noqa: E402
from .store import Store, StoreError, deep_merge  # noqa: E402

log = logging.getLogger("briod")

BUS_NAME = "io.github.BrioControl.Daemon"
OBJECT_PATH = "/io/github/BrioControl/Daemon"
IFACE = "io.github.BrioControl1"

HOTPLUG_DELAY_MS = 3000      # G HUB _scheduleDelayedCameraSettingsSync (0xBB8)
REAPPLY_ON_OPEN_MS = 500
SAVE_DELAY_MS = 500

INTROSPECTION = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetState"><arg type="s" direction="out"/></method>
    <method name="SetControl">
      <arg name="profile" type="s"/><arg name="slot" type="s"/><arg name="tab" type="s"/>
      <arg name="key" type="s"/><arg name="value_json" type="s"/>
    </method>
    <method name="SetControls">
      <arg name="profile" type="s"/><arg name="slot" type="s"/><arg name="changes_json" type="s"/>
      <arg name="errors_json" type="s" direction="out"/>
    </method>
    <method name="AssignCard">
      <arg name="profile" type="s"/><arg name="slot" type="s"/><arg name="tab" type="s"/><arg name="card" type="s"/>
    </method>
    <method name="ResetTab">
      <arg name="profile" type="s"/><arg name="slot" type="s"/><arg name="tab" type="s"/>
    </method>
    <method name="CreatePreset">
      <arg name="profile" type="s"/><arg name="slot" type="s"/><arg name="tab" type="s"/><arg name="name" type="s"/>
      <arg name="card" type="s" direction="out"/>
    </method>
    <method name="RenamePreset"><arg name="card" type="s"/><arg name="name" type="s"/></method>
    <method name="DuplicatePreset"><arg name="card" type="s"/><arg name="new_card" type="s" direction="out"/></method>
    <method name="DeletePreset"><arg name="card" type="s"/></method>
    <method name="CreateProfile"><arg name="name" type="s"/><arg name="profile" type="s" direction="out"/></method>
    <method name="RenameProfile"><arg name="profile" type="s"/><arg name="name" type="s"/></method>
    <method name="DeleteProfile"><arg name="profile" type="s"/></method>
    <method name="SetProfileApps"><arg name="profile" type="s"/><arg name="apps" type="as"/></method>
    <method name="SelectProfile"><arg name="profile" type="s"/></method>
    <method name="SetPersistent"><arg name="slot" type="s"/><arg name="enabled" type="b"/></method>
    <method name="SetDeviceSetting">
      <arg name="slot" type="s"/><arg name="key" type="s"/><arg name="value_json" type="s"/>
    </method>
    <method name="SetOption"><arg name="key" type="s"/><arg name="value_json" type="s"/></method>
    <method name="ApplyNow">
      <arg name="slot" type="s"/><arg name="errors_json" type="s" direction="out"/>
    </method>
    <method name="RegisterPreview"><arg name="slot" type="s"/><arg name="active" type="b"/></method>
    <method name="FocusChanged"><arg name="app_id" type="s"/></method>
    <method name="ImportGHub">
      <arg name="path" type="s"/><arg name="summary_json" type="s" direction="out"/>
    </method>
    <signal name="StateChanged"><arg name="reason" type="s"/></signal>
  </interface>
</node>
"""


class Daemon:
    def __init__(self, store: Store):
        self.store = store
        self.cameras: dict[str, Camera] = {}
        self.last_errors: dict[str, list[str]] = {}
        self.consumers: dict[str, list[dict]] = {}     # slot → [{pid, name, ids}] for D-Bus
        self.consumer_objs: dict[str, list[consumers.Consumer]] = {}
        self.first_seen: dict[int, float] = {}
        self.preview_pids: dict[str, set[int]] = {}
        self.focus_app = ""
        self.active_profile = store.data["selectedProfile"]
        self.inotify = consumers.Inotify()
        self.ctrl_watch: dict[str, int] = {}
        self.timers: dict[str, int] = {}
        self.conn: Gio.DBusConnection | None = None
        self.udev = GUdev.Client(subsystems=["video4linux"])
        self.udev.connect("uevent", self._on_uevent)
        GLib.io_add_watch(self.inotify.fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_inotify)
        self.focus = None
        self.tray = None

    # ------------------------------------------------------------- utilities
    def _timer(self, key: str, ms: int, fn, *args):
        """(Re)arm a one-shot named timer; a new arm cancels the pending one."""
        old = self.timers.pop(key, None)
        if old:
            GLib.source_remove(old)

        def fire():
            self.timers.pop(key, None)
            fn(*args)
            return GLib.SOURCE_REMOVE
        self.timers[key] = GLib.timeout_add(ms, fire)

    def save_later(self):
        self._timer("save", SAVE_DELAY_MS, self.store.save)

    def changed(self, reason="state"):
        if self.conn:
            self._timer(f"sig:{reason}", 50, self._emit, reason)
        if self.tray:
            self._timer("tray", 100, self.tray.rebuild)

    def _emit(self, reason):
        try:
            self.conn.emit_signal(None, OBJECT_PATH, IFACE, "StateChanged",
                                  GLib.Variant("(s)", (reason,)))
        except GLib.Error as e:
            log.debug("emit failed: %s", e)

    # ----------------------------------------------------------- hotplug
    def start(self):
        self.rescan(apply=True)
        if self.store.data["options"].get("focusSwitch"):
            self._start_focus()

    def _start_focus(self):
        from .focus import FocusWatcher
        if self.focus is None:
            self.focus = FocusWatcher(self.on_focus)
            if not self.focus.start():
                log.info("no Sway/Hyprland IPC found; waiting for FocusChanged over D-Bus")

    def _on_uevent(self, _client, action, device):
        if action == "add":
            log.info("hotplug add %s; applying in %d ms", device.get_device_file(), HOTPLUG_DELAY_MS)
            self._timer("hotplug", HOTPLUG_DELAY_MS, self.rescan, True)
        elif action == "remove":
            self.rescan(apply=False)

    def rescan(self, apply=False):
        found = {c.slot: c for c in uvc.find_cameras(brio.VID, brio.PIDS)}
        for slot in list(self.cameras):
            cam = self.cameras[slot]
            if slot not in found or found[slot].devnode != cam.info.devnode:
                self._detach(slot)
        for slot, info in found.items():
            if slot not in self.cameras:
                try:
                    self._attach(info)
                except OSError as e:
                    log.error("cannot open %s: %s", info.devnode, e)
                    continue
                if apply:
                    self.apply(slot, reason="hotplug")
        self.changed("devices")

    def _attach(self, info: uvc.CameraInfo):
        cam = Camera(info)
        self.cameras[cam.slot] = cam
        log.info("attached %s (%s, fw %s, xu %s)", cam.slot, info.devnode, cam.firmware, cam.xu)
        fd = cam.watch_ctrl_events()
        self.ctrl_watch[cam.slot] = GLib.io_add_watch(
            fd, GLib.PRIORITY_DEFAULT, GLib.IO_PRI, self._on_ctrl_event, cam.slot)
        for n in info.nodes:
            try:
                self.inotify.add(n)
            except OSError as e:
                log.warning("inotify %s: %s", n, e)

    def _detach(self, slot):
        cam = self.cameras.pop(slot)
        w = self.ctrl_watch.pop(slot, None)
        if w:
            GLib.source_remove(w)
        for n in cam.info.nodes:
            self.inotify.remove(n)
        cam.close()
        self.consumers.pop(slot, None)
        self.consumer_objs.pop(slot, None)
        log.info("detached %s", slot)

    # -------------------------------------------------------------- applying
    def expected(self, slot) -> dict[str, dict]:
        s = self.store.effective_settings(self.active_profile, slot)
        if self.store.data["options"]["driftPolicy"] == "device":
            s = deep_merge(s, self.store.adjustments(slot))
        return s

    def apply(self, slot, tabs=APPLY_ORDER, reason="", partial: dict | None = None) -> list[str]:
        cam = self.cameras.get(slot)
        if cam is None:
            return [f"{slot} is not connected"]
        dev_settings = self.store.device_settings(slot)
        if dev_settings.get("disabled"):
            return []
        settings = partial if partial is not None else self.expected(slot)
        errors = cam.apply(settings, tabs, None if partial is not None else dev_settings)
        self.last_errors[slot] = errors
        log.info("applied %s to %s (%s)%s", ",".join(t for t in APPLY_ORDER if t in tabs and t in settings),
                 slot, reason, f" errors: {errors}" if errors else "")
        return errors

    def apply_all(self, reason):
        for slot in self.cameras:
            self.apply(slot, reason=reason)

    # ----------------------------------------------------- drift / consumers
    def _on_ctrl_event(self, fd, cond, slot):
        cam = self.cameras.get(slot)
        if cam is None:
            return GLib.SOURCE_REMOVE
        events = cam.pending_ctrl_events()
        if events:
            log.info("%s: external change %s", slot, events)
            self._timer(f"drift:{slot}", 150, self.check_drift, slot)
        return GLib.SOURCE_CONTINUE

    def check_drift(self, slot):
        cam = self.cameras.get(slot)
        if cam is None or self.store.device_settings(slot).get("disabled"):
            return
        wanted = self.store.effective_settings(self.active_profile, slot)
        d = drift(wanted, cam.snapshot())
        policy = self.store.data["options"]["driftPolicy"]
        if policy == "device":
            if d != self.store.adjustments(slot):
                self.store.data["adjustments"][slot] = d
                log.info("%s: manual adjustment recorded %s", slot, d)
                self.save_later()
                self.changed("adjustment")
        elif d:
            log.info("%s: profile wins; reverting %s", slot, d)
            self.apply(slot, tabs=tuple(d), reason="drift")

    def _on_inotify(self, fd, cond):
        for path, mask in self.inotify.read():
            for slot, cam in self.cameras.items():
                if path in cam.info.nodes:
                    opened = bool(mask & consumers.IN_OPEN)
                    self._timer(f"scan:{slot}", REAPPLY_ON_OPEN_MS if opened else 200,
                                self.scan_consumers, slot, opened)
        return GLib.SOURCE_CONTINUE

    def scan_consumers(self, slot, opened=False):
        cam = self.cameras.get(slot)
        if cam is None:
            return
        foreign = consumers.holders(cam.info.nodes, exclude={os.getpid()})
        now = time.monotonic()
        for c in foreign:
            self.first_seen.setdefault(c.pid, now)
        live = {c.pid for cs in self.consumer_objs.values() for c in cs} | {c.pid for c in foreign}
        for pid in list(self.first_seen):
            if pid not in live:
                del self.first_seen[pid]
        previews = self.preview_pids.get(slot, set())
        for pid in list(previews):
            if not os.path.exists(f"/proc/{pid}"):
                previews.discard(pid)
        users = [c for c in foreign if c.pid not in previews]
        users.sort(key=lambda c: self.first_seen.get(c.pid, now), reverse=True)
        old = [c["pid"] for c in self.consumers.get(slot, [])]
        self.consumers[slot] = [{"pid": c.pid, "name": c.display_name, "ids": c.identifiers()}
                                for c in users]
        self.consumer_objs[slot] = users
        switched = self.update_active_profile()
        if opened and foreign and not switched:
            # Firmware may reset controls on stream start; uvcvideo restores only
            # cached standard controls.  Re-apply (cheap, idempotent).
            self.apply(slot, reason="camera opened")
        if not opened:
            self.check_xu_drift(slot)
        if old != [c.pid for c in users]:
            self.changed("consumers")

    def check_xu_drift(self, slot):
        """XU writes raise no V4L2 events; compare FOV after a foreign close."""
        cam = self.cameras.get(slot)
        if cam and cam.xu["fov"] is not None:
            self.check_drift(slot)

    def update_active_profile(self) -> bool:
        opts = self.store.data["options"]
        target = None
        if opts.get("autoSwitch"):
            users = [c for cs in self.consumer_objs.values() for c in cs]
            users.sort(key=lambda c: self.first_seen.get(c.pid, 0), reverse=True)
            for c in users:
                target = self.store.match_profile(c.identifiers())
                if target:
                    break
        if target is None and opts.get("focusSwitch") and self.focus_app:
            target = self.store.match_profile([self.focus_app])
        if target is None:
            target = self.store.data["selectedProfile"]
        if target != self.active_profile:
            log.info("profile %s → %s", self.active_profile, target)
            self.active_profile = target
            for slot in self.cameras:
                self.store.clear_adjustments(slot)
            self.apply_all("profile switch")
            self.changed("profile")
            return True
        return False

    def on_focus(self, app_id):
        self.focus_app = app_id or ""
        self.update_active_profile()
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- state
    def state(self) -> dict:
        devices = []
        for slot, cam in self.cameras.items():
            devices.append({
                "slot": slot, "name": cam.info.name, "devnode": cam.info.devnode,
                "nodes": cam.info.nodes, "serial": cam.info.serial, "firmware": cam.firmware,
                "capabilities": cam.capabilities(), "resolutions": cam.resolutions(),
                "assignments": self.store.assignments(self.active_profile, slot),
                "settings": self.expected(slot),
                "adjustments": self.store.adjustments(slot),
                "deviceSettings": self.store.device_settings(slot),
                "persistent": self.store.persistent_enabled(slot),
                "consumers": self.consumers.get(slot, []),
                "errors": self.last_errors.get(slot, []),
            })
        profiles = []
        for p in self.store.data["profiles"]:
            profiles.append({"id": p["id"], "name": p["name"], "apps": p.get("apps", []),
                             "assignments": {s: self.store.assignments(p["id"], s) for s in self.cameras}})
        return {"devices": devices, "profiles": profiles, "cards": self.store.all_cards(),
                "activeProfile": self.active_profile,
                "selectedProfile": self.store.data["selectedProfile"],
                "options": self.store.data["options"], "focusApp": self.focus_app,
                "tabs": list(brio.TABS)}

    # ------------------------------------------------------------- mutations
    def _pid(self, profile):
        return profile or self.active_profile

    def set_controls(self, profile, slot, changes: list[tuple[str, str, object]]) -> list[str]:
        pid = self._pid(profile)
        partial: dict[str, dict] = {}
        for tab, key, value in changes:
            card = self.store.edit(pid, slot, tab, key, value)
            top = key.split(".")[0]
            self.store.adjustments(slot).get(tab, {}).pop(top, None)
            partial.setdefault(tab, {})[top] = copy.deepcopy(card["settings"].get(top))
        errors = []
        if pid == self.active_profile:
            errors = self.apply(slot, tabs=tuple(partial), reason="edit", partial=partial)
        self.save_later()
        self.changed("settings")
        return errors

    def assignment_changed(self, profile, slot, tabs):
        pid = self._pid(profile)
        for t in tabs:
            self.store.clear_adjustments(slot, t)
        if pid == self.active_profile:
            self.apply(slot, tabs=tuple(tabs), reason="card assigned")
        self.save_later()
        self.changed("settings")

    def set_device_setting(self, slot, key, value):
        self.store.set_device_setting(slot, key, value)
        top = key.split(".")[0]
        if top == "disabled" and not value:
            self.apply(slot, reason="re-enabled")
        elif top in ("antiFlicker", "microphone") and slot in self.cameras:
            ds = self.store.device_settings(slot)
            if not ds.get("disabled"):
                errs = self.cameras[slot].apply({}, (), {top: ds[top]})
                self.last_errors[slot] = errs
        self.save_later()
        self.changed("device")

    def set_option(self, key, value):
        if key == "driftPolicy" and value not in ("device", "profile"):
            raise StoreError("driftPolicy must be 'device' or 'profile'")
        if key not in self.store.data["options"]:
            raise StoreError(f"unknown option {key!r}")
        self.store.data["options"][key] = value
        if key == "focusSwitch" and value:
            self._start_focus()
        if key == "driftPolicy":
            for slot in self.cameras:
                self.store.clear_adjustments(slot)
                self.apply(slot, reason="drift policy")
        self.update_active_profile()
        self.save_later()
        self.changed("options")

    # ---------------------------------------------------------------- D-Bus
    def _on_call(self, conn, sender, path, iface, method, params, inv):
        args = params.unpack()
        try:
            result = self.dispatch(method, args, sender)
        except (StoreError, KeyError, ValueError, OSError) as e:
            inv.return_dbus_error(f"{IFACE}.Error", str(e))
            return
        if result is None:
            inv.return_value(None)
        else:
            inv.return_value(GLib.Variant("(s)", (result,)))

    def _sender_pid(self, sender) -> int:
        r = self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "GetConnectionUnixProcessID", GLib.Variant("(s)", (sender,)),
                                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None)
        return r.unpack()[0]

    def dispatch(self, method, a, sender):
        st = self.store
        if method == "GetState":
            return json.dumps(self.state())
        if method == "SetControl":
            profile, slot, tab, key, value = a
            self.set_controls(profile, slot, [(tab, key, json.loads(value))])
            return None
        if method == "SetControls":
            profile, slot, changes = a
            errs = self.set_controls(profile, slot, [tuple(c) for c in json.loads(changes)])
            return json.dumps(errs)
        if method == "AssignCard":
            profile, slot, tab, card = a
            st.assign(self._pid(profile), slot, tab, card)
            self.assignment_changed(profile, slot, [tab])
            return None
        if method == "ResetTab":
            profile, slot, tab = a
            st.assign(self._pid(profile), slot, tab, f"default:{tab}")
            self.assignment_changed(profile, slot, [tab])
            return None
        if method == "CreatePreset":
            profile, slot, tab, name = a
            pid = self._pid(profile)
            src = st.card(st.assigned_card_id(pid, slot, tab))
            card = st.create_card(tab, name, src["settings"])
            st.assign(pid, slot, tab, card["id"])
            self.assignment_changed(profile, slot, [tab])
            return card["id"]
        if method == "RenamePreset":
            st.rename_card(*a)
        elif method == "DuplicatePreset":
            card = st.duplicate_card(a[0])
            self.save_later()
            self.changed("cards")
            return card["id"]
        elif method == "DeletePreset":
            st.delete_card(a[0])
            self.apply_all("preset deleted")
        elif method == "CreateProfile":
            p = st.create_profile(a[0])
            self.save_later()
            self.changed("profiles")
            return p["id"]
        elif method == "RenameProfile":
            st.rename_profile(*a)
        elif method == "DeleteProfile":
            st.delete_profile(a[0])
            self.update_active_profile()
        elif method == "SetProfileApps":
            st.set_profile_apps(a[0], a[1])
            self.update_active_profile()
        elif method == "SelectProfile":
            st.profile(a[0])
            st.data["selectedProfile"] = a[0]
            self.update_active_profile()
        elif method == "SetPersistent":
            slot, enabled = a
            st.set_persistent(slot, enabled, self.active_profile)
            self.assignment_changed("", slot, list(brio.TABS))
        elif method == "SetDeviceSetting":
            slot, key, value = a
            self.set_device_setting(slot, key, json.loads(value))
        elif method == "SetOption":
            self.set_option(a[0], json.loads(a[1]))
        elif method == "ApplyNow":
            slot = a[0]
            errs = []
            for s in ([slot] if slot else list(self.cameras)):
                errs += self.apply(s, reason="requested")
            self.changed("applied")
            return json.dumps(errs)
        elif method == "RegisterPreview":
            slot, active = a
            pid = self._sender_pid(sender)
            pids = self.preview_pids.setdefault(slot, set())
            (pids.add if active else pids.discard)(pid)
        elif method == "FocusChanged":
            self.on_focus(a[0])
        elif method == "ImportGHub":
            from .ghub_import import import_settings_db
            summary = import_settings_db(a[0], st, list(self.cameras))
            self.apply_all("import")
            self.save_later()
            self.changed("profiles")
            return json.dumps(summary)
        else:
            raise StoreError(f"unknown method {method}")
        self.save_later()
        self.changed(method)
        return None

    def export(self, conn, tray=True):
        self.conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        conn.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        if tray:
            from .tray import Tray
            self.tray = Tray(self)
            self.tray.export(conn)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="briod", description="BRIO control daemon")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--replace", action="store_true", help="replace a running instance")
    ap.add_argument("--no-tray", action="store_true", help="don't show a system tray icon")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    loop = GLib.MainLoop()
    daemon = Daemon(Store())

    def on_bus(conn, _name):
        daemon.export(conn, tray=not args.no_tray)

    def on_acquired(_conn, name):
        log.info("owning %s", name)
        daemon.start()

    def on_lost(_conn, name):
        log.error("could not own %s (another briod running?)", name)
        loop.quit()

    flags = Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT
    if args.replace:
        flags |= Gio.BusNameOwnerFlags.REPLACE
    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, flags, on_bus, on_acquired, on_lost)

    def stop(*_):
        daemon.store.save()
        loop.quit()
        return GLib.SOURCE_REMOVE
    _signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, stop)
    _signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, stop)
    loop.run()


if __name__ == "__main__":
    main()
