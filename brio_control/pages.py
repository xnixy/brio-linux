"""Crop / Exposure / Image / Focus / Device pages (G HUB webcam panels)."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from briod.models import brio

from .widgets import ChoiceRow, ScaleRow, ask_text, set_switch, switch_row


def _val(entry, default=None):
    return brio.setting_value(entry, default)


def _mode(entry, default="AUTO"):
    return brio.setting_mode(entry, default)


class TabPage(Adw.PreferencesPage):
    """A card-backed tab: preset selector + the tab's controls."""

    tab = ""
    title = ""
    icon = ""

    def __init__(self, win, dev):
        super().__init__(title=self.title, icon_name=self.icon)
        self.win = win
        self.slot = dev["slot"]
        self.caps = dev["capabilities"]
        self.card_ids: list[str] = []
        self._updating = False
        self._build_presets()
        self.build()

    # ------------------------------------------------------------- helpers
    def set(self, key, value):
        self.win.client.set_control(self.slot, self.tab, key, value)

    def set_many(self, changes):
        self.win.client.set_controls(self.slot, [(self.tab, k, v) for k, v in changes])

    def cap(self, name):
        return self.caps.get(name)

    # ------------------------------------------------------------ presets
    def _build_presets(self):
        group = Adw.PreferencesGroup()
        self.preset_row = Adw.ComboRow(title="Preset")
        self.preset_model = Gtk.StringList()
        self.preset_row.set_model(self.preset_model)
        self.preset_row.connect("notify::selected", self._preset_selected)

        menu = Gio.Menu()
        menu.append("Save as New Preset…", "preset.new")
        menu.append("Rename…", "preset.rename")
        menu.append("Duplicate", "preset.duplicate")
        menu.append("Delete", "preset.delete")
        section = Gio.Menu()
        section.append("Reset to Default", "preset.reset")
        menu.append_section(None, section)
        btn = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                             valign=Gtk.Align.CENTER, css_classes=["flat"],
                             tooltip_text="Preset actions")
        self.preset_row.add_suffix(btn)

        self.actions = Gio.SimpleActionGroup()
        for name, fn in (("new", self._new), ("rename", self._rename), ("duplicate", self._duplicate),
                         ("delete", self._delete), ("reset", self._reset)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, fn=fn: fn())
            self.actions.add_action(a)
        self.insert_action_group("preset", self.actions)
        group.add(self.preset_row)
        self.add(group)

    def _current_card(self):
        i = self.preset_row.get_selected()
        if 0 <= i < len(self.card_ids):
            return self.win.card(self.card_ids[i])
        return None

    def _preset_selected(self, row, _p):
        if self._updating:
            return
        i = row.get_selected()
        if 0 <= i < len(self.card_ids):
            self.win.client.call("AssignCard", "(ssss)", "", self.slot, self.tab, self.card_ids[i])

    def _new(self):
        ask_text(self.win, "New Preset", f"Save the current {self.title.lower()} settings as a preset.",
                 "", "Create",
                 lambda name: self.win.client.call("CreatePreset", "(ssss)", "", self.slot, self.tab, name))

    def _rename(self):
        c = self._current_card()
        if c:
            ask_text(self.win, "Rename Preset", None, c["name"], "Rename",
                     lambda name: self.win.client.call("RenamePreset", "(ss)", c["id"], name))

    def _duplicate(self):
        c = self._current_card()
        if c:
            self.win.client.call("DuplicatePreset", "(s)", c["id"],
                                 on_result=lambda cid: self.win.client.call(
                                     "AssignCard", "(ssss)", "", self.slot, self.tab, cid))

    def _delete(self):
        c = self._current_card()
        if c:
            self.win.client.call("DeletePreset", "(s)", c["id"])

    def _reset(self):
        self.win.client.call("ResetTab", "(sss)", "", self.slot, self.tab)

    def update_presets(self, state, dev):
        assigned = dev["assignments"].get(self.tab)
        profile = state["activeProfile"]
        cards = [c for c in state["cards"] if c["tab"] == self.tab]
        manual_prefix = "manual:"
        manual = [c for c in cards if c["id"].startswith(manual_prefix)
                  and c["id"].endswith(f":{self.slot}:{self.tab}")
                  and (c["id"].startswith(f"manual:{profile}:") or c["id"].startswith("manual:persistent:"))]
        builtin = [c for c in cards if c.get("readOnly")]
        user = [c for c in cards if not c.get("readOnly") and not c["id"].startswith(manual_prefix)]
        ordered = manual + builtin + user
        labels = []
        for c in ordered:
            if c["id"].startswith(manual_prefix):
                labels.append("Manual")
            elif c.get("readOnly"):
                labels.append(f"{c['name']} (built-in)")
            else:
                labels.append(c["name"])
        self._updating = True
        if [c["id"] for c in ordered] != self.card_ids:
            self.card_ids = [c["id"] for c in ordered]
            self.preset_model.splice(0, self.preset_model.get_n_items(), labels)
        if assigned in self.card_ids:
            self.preset_row.set_selected(self.card_ids.index(assigned))
        self._updating = False
        card = self.win.card(assigned) or {}
        writable = not card.get("readOnly") and not card.get("manualCard")
        self.actions.lookup_action("rename").set_enabled(writable)
        self.actions.lookup_action("delete").set_enabled(writable)

    # ------------------------------------------------------------- subclass
    def build(self):
        raise NotImplementedError

    def update(self, state, dev):
        self.update_presets(state, dev)
        self.refresh(dev["settings"][self.tab], dev)

    def refresh(self, s, dev):
        raise NotImplementedError


class CropPage(TabPage):
    tab, title, icon = "crop", "Crop", "zoom-fit-best-symbolic"

    def build(self):
        g = Adw.PreferencesGroup(title="Field of View")
        self.fov = None
        if "fov" in self.caps:
            self.fov = ChoiceRow("Field of view", [(v, f"{v}°") for v in self.caps["fov"]["values"]],
                                 lambda v: self.set("fov", v))
            g.add(self.fov)
        z = self.cap("zoom_absolute")
        self.zoom = None
        if z:
            self.zoom = ScaleRow("Zoom", z["minimum"], z["maximum"], max(z["step"], 1),
                                 lambda v: f"{int(v)}%", lambda v: self.set("zoom.value", int(v)))
            g.add(self.zoom)
        self.add(g)

        self.pan = self.cap("pan_absolute")
        self.tilt = self.cap("tilt_absolute")
        self.pt_row = None
        if self.pan and self.tilt:
            pg = Adw.PreferencesGroup(title="Pan and Tilt",
                                      description="Available when zoomed in")
            self.pt_row = Adw.ActionRow(title="Position")
            grid = Gtk.Grid(row_spacing=4, column_spacing=4, margin_top=6, margin_bottom=6,
                            valign=Gtk.Align.CENTER)
            buttons = (("go-up-symbolic", 1, 0, 0, 1), ("go-previous-symbolic", 0, 1, -1, 0),
                       ("go-next-symbolic", 2, 1, 1, 0), ("go-down-symbolic", 1, 2, 0, -1))
            for icon, col, row, dx, dy in buttons:
                b = Gtk.Button(icon_name=icon, css_classes=["circular"])
                b.connect("clicked", self._step, dx, dy)
                grid.attach(b, col, row, 1, 1)
            c = Gtk.Button(icon_name="find-location-symbolic", css_classes=["circular"],
                           tooltip_text="Center")
            c.connect("clicked", lambda *_: self.set_many([("pan.value", 0), ("tilt.value", 0)]))
            grid.attach(c, 1, 1, 1, 1)
            self.pt_label = Gtk.Label(css_classes=["dim-label", "numeric"])
            self.pt_row.add_suffix(self.pt_label)
            self.pt_row.add_suffix(grid)
            pg.add(self.pt_row)
            self.add(pg)
        self._s = {}

    def _step(self, _b, dx, dy):
        pan = _val(self._s.get("pan"), 0) + dx * max(self.pan["step"], 1)
        tilt = _val(self._s.get("tilt"), 0) + dy * max(self.tilt["step"], 1)
        pan = max(self.pan["minimum"], min(self.pan["maximum"], pan))
        tilt = max(self.tilt["minimum"], min(self.tilt["maximum"], tilt))
        changes = []
        if dx:
            changes.append(("pan.value", pan))
        if dy:
            changes.append(("tilt.value", tilt))
        self.set_many(changes)

    def refresh(self, s, dev):
        self._s = s
        if self.fov:
            self.fov.set_value(s.get("fov", 90))
        if self.zoom:
            self.zoom.set_value(_val(s.get("zoom"), 100))
        if self.pt_row:
            z = self.cap("zoom_absolute")
            at_min = _val(s.get("zoom"), 100) <= (z["minimum"] if z else 100)
            # G HUB: disabled at minimum zoom when FOV ≥ 90 or an FOV capability
            # exists (has_pan_and_tilt_on_min_zoom is false for BRIO).
            disabled = at_min and (s.get("fov", 90) >= 90 or "fov" in self.caps)
            self.pt_row.set_sensitive(not disabled)
            step = max(self.pan["step"], 1)
            self.pt_label.set_label(f"{_val(s.get('pan'), 0) // step:+d}, {_val(s.get('tilt'), 0) // step:+d}")


class ExposurePage(TabPage):
    tab, title, icon = "exposure", "Exposure", "display-brightness-symbolic"

    def build(self):
        g = Adw.PreferencesGroup()
        self.auto = None
        if self.cap("auto_exposure"):
            self.auto = switch_row("Auto exposure",
                                   lambda on: self.set("exposure.mode", "AUTO" if on else "MANUAL"))
            g.add(self.auto)
        self.shutter = None
        if self.cap("exposure_time_absolute"):
            table = [v for v, _ in brio.EXPOSURE_TABLE]
            self.shutter = ScaleRow("Shutter speed", 0, len(table) - 1, 1,
                                    lambda i: f"1/{table[int(i)]}s",
                                    lambda i: self.set("exposure.value", table[int(i)]))
            g.add(self.shutter)
        self.gain = None
        if self.cap("gain"):
            self.gain = ScaleRow("ISO", 100, 1600, 100, lambda v: f"{int(v)}",
                                 lambda v: self.set("gain.value", int(v)))
            g.add(self.gain)
        self.lowlight = None
        if self.cap("exposure_dynamic_framerate"):
            self.lowlight = switch_row(
                "Low light compensation",
                lambda on: self.set("priority", brio.PRIORITY_EXPOSURE if on else brio.PRIORITY_FRAME_RATE),
                subtitle="RightLight: lower the frame rate to brighten dark scenes")
            g.add(self.lowlight)
        self.hdr = None
        if self.cap("backlight_compensation"):
            self.hdr = switch_row("HDR", lambda on: self.set("hdr", on))
            g.add(self.hdr)
        self.add(g)

    def refresh(self, s, dev):
        ex = s.get("exposure", {})
        auto = _mode(ex) == "AUTO"
        if self.auto:
            set_switch(self.auto, auto)
        if self.shutter:
            table = [v for v, _ in brio.EXPOSURE_TABLE]
            v = _val(ex, 60)
            self.shutter.set_value(table.index(v) if v in table else table.index(60))
            self.shutter.set_sensitive(not auto)
        if self.gain:
            self.gain.set_value(_val(s.get("gain"), 100))
            self.gain.set_sensitive(not auto)
        if self.lowlight:
            set_switch(self.lowlight, s.get("priority") == brio.PRIORITY_EXPOSURE)
            self.lowlight.set_sensitive(auto)
        if self.hdr:
            set_switch(self.hdr, s.get("hdr", True))


class ImagePage(TabPage):
    tab, title, icon = "image", "Image", "color-select-symbolic"

    def build(self):
        g = Adw.PreferencesGroup()
        self.sliders = {}
        for key, title in (("brightness", "Brightness"), ("contrast", "Contrast"),
                           ("saturation", "Color intensity"), ("sharpness", "Sharpness")):
            c = self.cap(key)
            if not c:
                continue
            lo, hi = c["minimum"], c["maximum"]
            row = ScaleRow(title, 0, 100, 1, lambda p: f"{int(p)}%",
                           lambda p, key=key, lo=lo, hi=hi: self.set(f"{key}.value", brio.pct_to_raw(p, lo, hi)))
            self.sliders[key] = (row, lo, hi)
            g.add(row)
        self.add(g)

        wg = Adw.PreferencesGroup(title="White Balance")
        self.wb_auto = None
        self.wb = None
        if self.cap("white_balance_automatic"):
            self.wb_auto = switch_row("Auto white balance",
                                      lambda on: self.set("whiteBalance.mode", "AUTO" if on else "MANUAL"))
            wg.add(self.wb_auto)
        t = self.cap("white_balance_temperature")
        if t:
            self.wb = ScaleRow("Temperature", t["minimum"], t["maximum"], max(t["step"], 10),
                               lambda v: f"{int(v)}K", lambda v: self.set("whiteBalance.value", int(v)))
            wg.add(self.wb)
        if self.wb_auto or self.wb:
            self.add(wg)

    def refresh(self, s, dev):
        for key, (row, lo, hi) in self.sliders.items():
            row.set_value(brio.raw_to_pct(_val(s.get(key), 128), lo, hi))
        wb = s.get("whiteBalance", {})
        auto = _mode(wb) == "AUTO"
        if self.wb_auto:
            set_switch(self.wb_auto, auto)
        if self.wb:
            self.wb.set_value(_val(wb, 4000))
            self.wb.set_sensitive(not auto or not self.wb_auto)


class FocusPage(TabPage):
    tab, title, icon = "focus", "Focus", "find-location-symbolic"

    def build(self):
        g = Adw.PreferencesGroup()
        self.auto = None
        self.slider = None
        if self.cap("focus_automatic_continuous"):
            self.auto = switch_row("Autofocus",
                                   lambda on: self.set("focus.mode", "AUTO" if on else "MANUAL"))
            g.add(self.auto)
        f = self.cap("focus_absolute")
        if f:
            self.lo, self.hi, self.step = f["minimum"], f["maximum"], max(f["step"], 1)

            def commit(p):
                raw = brio.pct_to_raw(p, self.lo, self.hi)
                raw = self.lo + round((raw - self.lo) / self.step) * self.step
                self.set("focus.value", min(raw, self.hi))
            self.slider = ScaleRow("Focus", 0, 100, 1, lambda p: f"{int(p)}%", commit)
            g.add(self.slider)
        self.add(g)

    def refresh(self, s, dev):
        f = s.get("focus", {})
        auto = _mode(f) == "AUTO"
        if self.auto:
            set_switch(self.auto, auto)
        if self.slider:
            self.slider.set_value(brio.raw_to_pct(_val(f, 0), self.lo, self.hi))
            self.slider.set_sensitive(not auto)


class DevicePage(Adw.PreferencesPage):
    """Device settings (not card-backed)."""

    def __init__(self, win, dev):
        super().__init__(title="Device", icon_name="emblem-system-symbolic")
        self.win = win
        self.slot = dev["slot"]
        caps = dev["capabilities"]
        setd = lambda k, v: win.client.set_device_setting(self.slot, k, v)  # noqa: E731

        g = Adw.PreferencesGroup(title="Device Settings")
        self.flicker = None
        if "power_line_frequency" in caps:
            self.flicker = ChoiceRow("Anti-flicker", [(50, "50 Hz"), (60, "60 Hz")],
                                     lambda v: setd("antiFlicker", v),
                                     subtitle="Match your mains frequency")
            g.add(self.flicker)
        self.focus_loss = switch_row("Stop preview when app is unfocused",
                                     lambda on: setd("stopVideoOnAppFocusLoss", on),
                                     subtitle="Frees the camera for other apps")
        g.add(self.focus_loss)
        self.disabled = switch_row("Disable device in app",
                                   lambda on: setd("disabled", on),
                                   subtitle="Broadcast mode: stop applying profiles to this camera")
        g.add(self.disabled)
        self.persistent = switch_row("Use same settings in every profile",
                                     lambda on: win.client.call("SetPersistent", "(sb)", self.slot, on),
                                     subtitle="Persistent lock")
        g.add(self.persistent)
        self.mic = None
        if "mic" in caps:
            self.mic = switch_row("Microphone", lambda on: setd("microphone", on),
                                  subtitle="Enable the built-in microphone")
            g.add(self.mic)
        self.add(g)

        b = Adw.PreferencesGroup(title="Behaviour")
        self.drift = Adw.ComboRow(title="When another app changes a setting",
                                  model=Gtk.StringList.new(["Keep the change", "Restore the profile"]))
        self.drift._updating = False
        self.drift.connect("notify::selected", self._drift)
        b.add(self.drift)
        self.autoswitch = switch_row("Switch profiles by camera app",
                                     lambda on: win.client.set_option("autoSwitch", on),
                                     subtitle="Use the profile whose apps include the app using the camera")
        b.add(self.autoswitch)
        self.focusswitch = switch_row("Switch profiles by focused window",
                                      lambda on: win.client.set_option("focusSwitch", on),
                                      subtitle="Sway, Hyprland, or the GNOME/KDE helper")
        b.add(self.focusswitch)
        self.tray = switch_row("Show icon in system tray",
                               lambda on: win.client.set_option("trayIcon", on),
                               subtitle="On GNOME this needs the AppIndicator extension")
        b.add(self.tray)
        self.add(b)

        info = Adw.PreferencesGroup(title="About This Camera")
        self.info_rows = {}
        for key, title in (("name", "Model"), ("serial", "Serial number"),
                           ("firmware", "Firmware"), ("devnode", "Device node")):
            r = Adw.ActionRow(title=title, subtitle_selectable=True, css_classes=["property"])
            self.info_rows[key] = r
            info.add(r)
        self.add(info)

    def _drift(self, row, _p):
        if not row._updating:
            self.win.client.set_option("driftPolicy", ["device", "profile"][row.get_selected()])

    def update(self, state, dev):
        ds = dev["deviceSettings"]
        if self.flicker:
            self.flicker.set_value(ds.get("antiFlicker", 60))
        set_switch(self.focus_loss, ds.get("stopVideoOnAppFocusLoss", True))
        set_switch(self.disabled, ds.get("disabled", False))
        set_switch(self.persistent, dev.get("persistent", False))
        if self.mic:
            set_switch(self.mic, ds.get("microphone") is not False)
        opts = state["options"]
        self.drift._updating = True
        self.drift.set_selected(0 if opts.get("driftPolicy") == "device" else 1)
        self.drift._updating = False
        set_switch(self.autoswitch, opts.get("autoSwitch", True))
        set_switch(self.focusswitch, opts.get("focusSwitch", False))
        set_switch(self.tray, opts.get("trayIcon", True))
        for key, r in self.info_rows.items():
            r.set_subtitle(GLib.markup_escape_text(str(dev.get(key) or "—")))


PAGES = (CropPage, ExposurePage, ImagePage, FocusPage)
