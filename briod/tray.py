"""System tray icon: StatusNotifierItem + com.canonical.dbusmenu over plain Gio.

Lives in the daemon (like G HUB's agent tray) so it stays while the window is
closed.  Shown by KDE, Sway/waybar, Hyprland bars, and GNOME with the
AppIndicator extension.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

from gi.repository import Gio, GLib

from .models import brio

try:                                       # GLib ≥ 2.86 moved it to GioUnix
    import gi
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix
    _DesktopAppInfo = GioUnix.DesktopAppInfo
except (ValueError, ImportError, AttributeError):
    _DesktopAppInfo = Gio.DesktopAppInfo

log = logging.getLogger("briod.tray")

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="Activate"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i"/><arg name="y" type="i"/></method>
    <method name="Scroll"><arg name="delta" type="i"/><arg name="orientation" type="s"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""


class MenuItem:
    def __init__(self, label="", action=None, *, toggle=None, checked=False, enabled=True,
                 separator=False, children=None, icon=None):
        self.id = 0
        self.label, self.action = label, action
        self.toggle, self.checked, self.enabled = toggle, checked, enabled
        self.separator, self.children, self.icon = separator, children or [], icon

    def props(self) -> dict[str, GLib.Variant]:
        if self.separator:
            return {"type": GLib.Variant("s", "separator")}
        p = {"label": GLib.Variant("s", self.label), "enabled": GLib.Variant("b", self.enabled)}
        if self.toggle:
            p["toggle-type"] = GLib.Variant("s", self.toggle)
            p["toggle-state"] = GLib.Variant("i", 1 if self.checked else 0)
        if self.children:
            p["children-display"] = GLib.Variant("s", "submenu")
        if self.icon:
            p["icon-name"] = GLib.Variant("s", self.icon)
        return p


def launch_gui():
    """Open (or raise) the GUI: installed desktop file, PATH, or source tree."""
    try:
        info = _DesktopAppInfo.new("io.github.BrioControl.desktop")
    except TypeError:                      # not installed: constructor returns NULL
        info = None
    if info is not None:
        try:
            info.launch([], None)
            return
        except GLib.Error as e:
            log.warning("desktop launch failed: %s", e)
    exe = shutil.which("brio-control")
    if exe:
        cmd = [exe]
        cwd = None
    else:   # running from a source checkout
        cmd = [sys.executable, "-m", "brio_control.main"]
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.Popen(cmd, cwd=cwd, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Tray:
    def __init__(self, daemon):
        self.d = daemon
        self.conn: Gio.DBusConnection | None = None
        self.revision = 1
        self.items: dict[int, MenuItem] = {}
        self.root = MenuItem()
        self.icon = "camera-web-symbolic"
        self.tooltip = "BRIO Control"
        self.status = "Active"
        self.regs = []
        self.watch = 0

    # ----------------------------------------------------------- lifecycle
    def export(self, conn: Gio.DBusConnection):
        self.conn = conn
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.regs = [conn.register_object(ITEM_PATH, sni, self._sni_call, self._sni_get, None),
                     conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None)]
        self.rebuild()
        self.watch = Gio.bus_watch_name_on_connection(conn, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                                      self._watcher_appeared, None)

    def _watcher_appeared(self, conn, _name, _owner):
        conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (conn.get_unique_name(),)), None, Gio.DBusCallFlags.NONE,
                  -1, None, self._registered, None)

    def _registered(self, conn, res, _):
        try:
            conn.call_finish(res)
            log.info("tray icon registered")
        except GLib.Error as e:
            log.warning("tray registration failed: %s", e.message)

    # --------------------------------------------------------------- model
    def rebuild(self):
        """Recompute menu + icon from daemon state and notify the host."""
        if self.conn is None:
            return
        d = self.d
        st = d.state()
        enabled = d.store.data["options"].get("trayIcon", True)
        items: list[MenuItem] = []
        devices = st["devices"]
        any_in_use = False
        any_disabled = False
        if not devices:
            items.append(MenuItem("No BRIO connected", enabled=False))
        for dev in devices:
            users = [c["name"] for c in dev["consumers"]]
            any_in_use |= bool(users)
            disabled = bool(dev["deviceSettings"].get("disabled"))
            any_disabled |= disabled
            head = dev["name"] + (f" — in use by {', '.join(users)}" if users else "")
            items.append(MenuItem(head, enabled=False))
            s = dev["settings"]
            caps = dev["capabilities"]
            slot = dev["slot"]
            if "backlight_compensation" in caps:
                items.append(MenuItem("HDR", self._set(slot, "exposure", "hdr", not s["exposure"].get("hdr")),
                                      toggle="checkmark", checked=bool(s["exposure"].get("hdr")),
                                      enabled=not disabled))
            if "fov" in caps:
                fov = s["crop"].get("fov", 90)
                items.append(MenuItem("Field of View", enabled=not disabled, children=[
                    MenuItem(f"{v}°", self._set(slot, "crop", "fov", v), toggle="radio", checked=fov == v)
                    for v in caps["fov"]["values"]]))
            af = s["focus"].get("focus", {})
            if "focus_automatic_continuous" in caps:
                auto = brio.setting_mode(af, "AUTO") == "AUTO"
                items.append(MenuItem("Autofocus",
                                      self._set(slot, "focus", "focus.mode", "MANUAL" if auto else "AUTO"),
                                      toggle="checkmark", checked=auto, enabled=not disabled))
            items.append(MenuItem("Disable Device in App", self._device(slot, "disabled", not disabled),
                                  toggle="checkmark", checked=disabled))
            items.append(MenuItem(separator=True))

        profiles = st["profiles"]
        if len(profiles) > 1 or devices:
            active = st["activeProfile"]
            items.append(MenuItem("Profile", children=[
                MenuItem(p["name"], self._select(p["id"]), toggle="radio", checked=p["id"] == active)
                for p in profiles]))
        items.append(MenuItem("Re-apply Settings", self._reapply, enabled=bool(devices)))
        items.append(MenuItem(separator=True))
        items.append(MenuItem("Open BRIO Control", launch_gui))

        self.root = MenuItem(children=items)
        self.items = {}
        self._number(self.root, [0])
        self.revision += 1

        icon = ("camera-disabled-symbolic" if any_disabled else
                "camera-video-symbolic" if any_in_use else "camera-web-symbolic")
        names = {p["id"]: p["name"] for p in profiles}
        tip = f"Profile: {names.get(st['activeProfile'], st['activeProfile'])}"
        if any_in_use:
            tip += "\nCamera in use"
        status = "Active" if enabled else "Passive"
        self._emit_menu("LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))
        if icon != self.icon:
            self.icon = icon
            self._emit_sni("NewIcon")
        if tip != self.tooltip:
            self.tooltip = tip
            self._emit_sni("NewToolTip")
        if status != self.status:
            self.status = status
            self._emit_sni("NewStatus", GLib.Variant("(s)", (status,)))

    def _number(self, item, counter):
        item.id = counter[0]
        self.items[item.id] = item
        for c in item.children:
            counter[0] += 1
            self._number(c, counter)

    # ------------------------------------------------------------- actions
    def _set(self, slot, tab, key, value):
        return lambda: self.d.set_controls("", slot, [(tab, key, value)])

    def _device(self, slot, key, value):
        return lambda: self.d.set_device_setting(slot, key, value)

    def _select(self, pid):
        def run():
            self.d.store.data["selectedProfile"] = pid
            self.d.update_active_profile()
            self.d.save_later()
            self.d.changed("profile")
        return run

    def _reapply(self):
        for slot in self.d.cameras:
            self.d.apply(slot, reason="tray")
        self.d.changed("applied")

    # ------------------------------------------------------------- D-Bus SNI
    def _sni_get(self, conn, sender, path, iface, prop):
        tooltip = ("", [], "BRIO Control", self.tooltip)
        return {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "brio-control"),
            "Title": GLib.Variant("s", "BRIO Control"),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon),
            "IconThemePath": GLib.Variant("s", ""),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", tooltip),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            launch_gui()
        inv.return_value(None)

    def _emit_sni(self, signal, params=None):
        try:
            self.conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", signal, params)
        except GLib.Error as e:
            log.debug("tray signal failed: %s", e)

    # ----------------------------------------------------------- D-Bus menu
    def _layout(self, item: MenuItem, depth: int) -> tuple:
        """(id, props, children) where each child is a variant-wrapped layout."""
        children = []
        if depth != 0:
            children = [GLib.Variant("(ia{sv}av)", self._layout(c, depth - 1)) for c in item.children]
        return (item.id, item.props(), children)

    def _menu_get(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        a = params.unpack()
        if method == "GetLayout":
            parent, depth, _names = a
            item = self.items.get(parent, self.root)
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(item, depth))))
        elif method == "GetGroupProperties":
            ids = a[0] or list(self.items)
            out = [(i, self.items[i].props()) for i in ids if i in self.items]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            item = self.items.get(a[0])
            v = item.props().get(a[1]) if item else None
            inv.return_value(GLib.Variant("(v)", (v if v is not None else GLib.Variant("s", ""),)))
        elif method == "Event":
            self._event(a[0], a[1])
            inv.return_value(None)
        elif method == "EventGroup":
            errors = []
            for item_id, event_id, _data, _ts in a[0]:
                if item_id not in self.items:
                    errors.append(item_id)
                self._event(item_id, event_id)
            inv.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _event(self, item_id, event_id):
        item = self.items.get(item_id)
        if event_id == "clicked" and item and item.action and item.enabled:
            # Run after the D-Bus reply so the host isn't blocked by device I/O.
            GLib.idle_add(lambda: (item.action(), GLib.SOURCE_REMOVE)[1])

    def _emit_menu(self, signal, params):
        try:
            self.conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", signal, params)
        except GLib.Error as e:
            log.debug("menu signal failed: %s", e)
