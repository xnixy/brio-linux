"""Main window: preview pane + Crop/Exposure/Image/Focus/Device tabs."""
from __future__ import annotations

import json

from gi.repository import Adw, Gio, GLib, Gtk

from .pages import PAGES, DevicePage
from .preview import Preview
from .profiles import ProfilesDialog


class Window(Adw.ApplicationWindow):
    def __init__(self, app, client):
        super().__init__(application=app, title="BRIO Control", default_width=1180, default_height=720)
        self.client = client
        self.slot = None
        self.built_key = None
        self.pages = []
        self.device_page = None
        self._cards = {}
        self._updating = False
        self.preview_wanted = False

        # ---- header
        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack(vexpand=True)
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        self.profile_model = Gtk.StringList()
        self.profile_ids = []
        self.profile_dd = Gtk.DropDown(model=self.profile_model, tooltip_text="Profile")
        self.profile_dd.connect("notify::selected", self._profile_chosen)
        header.pack_start(self.profile_dd)
        self.device_model = Gtk.StringList()
        self.device_slots = []
        self.device_dd = Gtk.DropDown(model=self.device_model, visible=False, tooltip_text="Camera")
        self.device_dd.connect("notify::selected", self._device_chosen)
        header.pack_start(self.device_dd)
        menu = Gio.Menu()
        menu.append("Profiles…", "win.profiles")
        menu.append("Import from G HUB…", "win.import")
        menu.append("Re-apply Settings", "win.apply")
        menu.append("About BRIO Control", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))
        for name, fn in (("profiles", self.show_profiles), ("import", self.import_ghub),
                         ("apply", lambda: client.call("ApplyNow", "(s)", self.slot or ""))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(a)

        # ---- banners
        self.daemon_banner = Adw.Banner(title="The BRIO control service (briod) is not running",
                                        button_label="Start")
        self.daemon_banner.connect("button-clicked", lambda *_: client.start_daemon())
        self.inuse_banner = Adw.Banner()
        self.disabled_banner = Adw.Banner(title="This camera is disabled in the app; profiles are not applied",
                                          button_label="Enable")
        self.disabled_banner.connect("button-clicked",
                                     lambda *_: client.set_device_setting(self.slot, "disabled", False))
        self.profile_banner = Adw.Banner()

        # ---- preview column
        self.preview = Preview()
        self.preview.connect("state", self._preview_state)
        bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.btn_preview = Gtk.ToggleButton(icon_name="camera-video-symbolic", tooltip_text="Preview")
        self.btn_preview.connect("toggled", self._toggle_preview)
        self.btn_grid = Gtk.ToggleButton(icon_name="view-grid-symbolic", tooltip_text="Grid")
        self.btn_grid.connect("toggled", lambda b: self._preview_setting("grid", b.get_active()))
        self.btn_mirror = Gtk.ToggleButton(icon_name="object-flip-horizontal-symbolic", tooltip_text="Mirror")
        self.btn_mirror.connect("toggled", lambda b: self._preview_setting("mirror", b.get_active()))
        self.res_model = Gtk.StringList()
        self.res_ids = []
        self.res_dd = Gtk.DropDown(model=self.res_model, tooltip_text="Preview resolution")
        self.res_dd.connect("notify::selected", self._res_chosen)
        snap = Gtk.Button(icon_name="camera-photo-symbolic", tooltip_text="Take snapshot")
        snap.connect("clicked", self._snapshot)
        for w in (self.btn_preview, self.btn_grid, self.btn_mirror):
            bar.append(w)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.res_dd)
        bar.append(snap)
        frame = Gtk.Frame(child=self.preview, margin_start=12, margin_top=12, margin_end=6,
                          css_classes=["view"])
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(frame)
        left.append(bar)

        self.empty = Adw.StatusPage(icon_name="camera-web-symbolic", title="No BRIO Connected",
                                    description="Plug in a Logitech BRIO to configure it")
        scroller = Gtk.ScrolledWindow(child=self.stack, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      min_content_width=380)
        self.content_stack = Gtk.Stack()
        paned = Gtk.Paned(start_child=left, end_child=scroller, shrink_start_child=True,
                          shrink_end_child=False, resize_end_child=False, position=740)
        self.content_stack.add_named(paned, "main")
        self.content_stack.add_named(self.empty, "empty")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for b in (self.daemon_banner, self.disabled_banner, self.inuse_banner, self.profile_banner):
            body.append(b)
        body.append(self.content_stack)
        self.toasts = Adw.ToastOverlay(child=body)
        tv = Adw.ToolbarView(content=self.toasts)
        tv.add_top_bar(header)
        self.set_content(tv)

        client.connect("state-changed", lambda *_: self.refresh())
        client.connect("availability", lambda *_: self.refresh())
        client.connect("error", lambda _c, msg: self.toast(msg))
        self.connect("notify::is-active", self._focus_changed)
        self.connect("close-request", self._on_close)
        self.refresh()

    # ------------------------------------------------------------- helpers
    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=4))

    def card(self, cid):
        return self._cards.get(cid)

    def dev(self):
        for d in self.client.state.get("devices", []):
            if d["slot"] == self.slot:
                return d
        return None

    # ------------------------------------------------------------- refresh
    def refresh(self):
        st = self.client.state
        self.daemon_banner.set_revealed(not self.client.available)
        devices = st.get("devices", [])
        self._cards = {c["id"]: c for c in st.get("cards", [])}
        self._updating = True
        # devices
        slots = [d["slot"] for d in devices]
        if slots != self.device_slots:
            self.device_slots = slots
            self.device_model.splice(0, self.device_model.get_n_items(),
                                     [f"{d['name']} ({d['serial']})" for d in devices])
        self.device_dd.set_visible(len(devices) > 1)
        if self.slot not in slots:
            self.slot = slots[0] if slots else None
        if self.slot:
            self.device_dd.set_selected(slots.index(self.slot))
        # profiles
        profiles = st.get("profiles", [])
        ids = [p["id"] for p in profiles]
        if ids != self.profile_ids or [p["name"] for p in profiles] != [
                self.profile_model.get_string(i) for i in range(self.profile_model.get_n_items())]:
            self.profile_ids = ids
            self.profile_model.splice(0, self.profile_model.get_n_items(), [p["name"] for p in profiles])
        sel = st.get("selectedProfile")
        if sel in ids:
            self.profile_dd.set_selected(ids.index(sel))
        self.profile_dd.set_sensitive(bool(ids))
        active = st.get("activeProfile")
        if active and active != sel and active in ids:
            self.profile_banner.set_title(
                f"Active profile switched automatically to “{profiles[ids.index(active)]['name']}”")
            self.profile_banner.set_revealed(True)
        else:
            self.profile_banner.set_revealed(False)
        self._updating = False

        dev = self.dev()
        if dev is None:
            self.content_stack.set_visible_child_name("empty")
            self.empty.set_description("Plug in a Logitech BRIO to configure it" if self.client.available
                                       else "Start the briod service to configure your camera")
            self.preview.stop()
            self.disabled_banner.set_revealed(False)
            self.inuse_banner.set_revealed(False)
            return
        self.content_stack.set_visible_child_name("main")
        key = (dev["slot"], json.dumps(dev["capabilities"], sort_keys=True))
        if key != self.built_key:
            self._build_pages(dev)
            self.built_key = key
        for p in self.pages:
            p.update(st, dev)
        self.device_page.update(st, dev)

        ds = dev["deviceSettings"]
        disabled = bool(ds.get("disabled"))
        self.disabled_banner.set_revealed(disabled)
        for p in self.pages:
            p.set_sensitive(not disabled)
        users = [c["name"] for c in dev.get("consumers", [])]
        self.inuse_banner.set_title(f"Camera in use by {', '.join(users)}. Controls still work; "
                                    "the preview is unavailable while another app streams" if users else "")
        self.inuse_banner.set_revealed(bool(users))
        self._refresh_preview(dev)

    def _build_pages(self, dev):
        for p in self.pages + ([self.device_page] if self.device_page else []):
            self.stack.remove(p)
        self.pages = [cls(self, dev) for cls in PAGES]
        for p in self.pages:
            self.stack.add_titled_with_icon(p, p.tab, p.title, p.icon)
        self.device_page = DevicePage(self, dev)
        self.stack.add_titled_with_icon(self.device_page, "device", "Device", "emblem-system-symbolic")

    # -------------------------------------------------------------- preview
    def _refresh_preview(self, dev):
        pv = dev["deviceSettings"].get("preview", {})
        self._updating = True
        res = dev["resolutions"]
        ids = [r["id"] for r in res]
        if ids != self.res_ids:
            self.res_ids = ids
            self.res_model.splice(0, self.res_model.get_n_items(), [r["label"] for r in res])
        rid = pv.get("resolution")
        if rid not in ids and ids:
            rid = "RES_720p30" if "RES_720p30" in ids else ids[0]
        if rid in ids:
            self.res_dd.set_selected(ids.index(rid))
        self.btn_grid.set_active(bool(pv.get("grid")))
        self.btn_mirror.set_active(bool(pv.get("mirror")))
        self.preview_wanted = bool(pv.get("preview", True))
        self.btn_preview.set_active(self.preview_wanted)
        self._updating = False
        mode = next((r for r in res if r["id"] == rid), None)
        self.preview.configure(dev["devnode"], mode, bool(pv.get("mirror")), bool(pv.get("grid")))
        self._sync_preview()

    def _sync_preview(self):
        dev = self.dev()
        want = (dev is not None and self.preview_wanted and
                (self.is_active() or not dev["deviceSettings"].get("stopVideoOnAppFocusLoss", True)))
        if want and not self.preview.running:
            users = [c["name"] for c in dev.get("consumers", [])]
            self.client.register_preview(self.slot, True)
            self.preview.start()
            if users:
                self.preview.show_message(f"Camera in use by {', '.join(users)}")
        elif not want and self.preview.running:
            self.preview.stop()
            if dev is not None and self.preview_wanted:
                self.preview.show_message("Preview paused while the window is unfocused")

    def _preview_state(self, _p, state, msg):
        if state == "stopped":
            self.client.register_preview(self.slot or "", False)
        elif state == "error":
            self.client.register_preview(self.slot or "", False)
            dev = self.dev()
            users = [c["name"] for c in dev.get("consumers", [])] if dev else []
            if msg == "busy" or users:
                self.preview.show_message(f"Camera in use by {', '.join(users) or 'another app'}")
            else:
                self.preview.show_message(f"Preview failed: {msg}")

    def _toggle_preview(self, b):
        if not self._updating:
            self._preview_setting("preview", b.get_active())

    def _preview_setting(self, key, value):
        if self._updating or not self.slot:
            return
        if key == "mirror":
            self.preview.set_mirror(value)
        elif key == "grid":
            self.preview.grid.set_visible(value)
        elif key == "preview":
            self.preview_wanted = value
            self._sync_preview()
        self.client.set_device_setting(self.slot, f"preview.{key}", value)

    def _res_chosen(self, dd, _p):
        i = dd.get_selected()
        if not self._updating and 0 <= i < len(self.res_ids):
            self.client.set_device_setting(self.slot, "preview.resolution", self.res_ids[i])

    def _focus_changed(self, *_):
        self._sync_preview()

    def _snapshot(self, _b):
        dev = self.dev()
        if not dev:
            return
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or GLib.get_home_dir()
        folder = GLib.build_filenamev([pictures, "Webcam Snapshots"])
        try:
            path = self.preview.snapshot(folder, dev["name"])
        except RuntimeError as e:
            self.toast(str(e))
            return
        t = Adw.Toast(title=f"Saved {GLib.path_get_basename(path)}", button_label="Open Folder", timeout=4)
        t.connect("button-clicked", lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(path))
                  .open_containing_folder(self, None, None, None))
        self.toasts.add_toast(t)

    # ------------------------------------------------------------ selectors
    def _profile_chosen(self, dd, _p):
        i = dd.get_selected()
        if not self._updating and 0 <= i < len(self.profile_ids):
            self.client.call("SelectProfile", "(s)", self.profile_ids[i])

    def _device_chosen(self, dd, _p):
        i = dd.get_selected()
        if not self._updating and 0 <= i < len(self.device_slots):
            self.preview.stop()
            self.slot = self.device_slots[i]
            self.refresh()

    def show_profiles(self):
        ProfilesDialog(self).present(self)

    def import_ghub(self):
        dlg = Gtk.FileDialog(title="Import G HUB settings.db")
        f = Gtk.FileFilter(name="G HUB settings")
        f.add_pattern("*.db")
        f.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)

        def done(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                return
            self.client.call("ImportGHub", "(s)", gfile.get_path(), on_result=lambda s: self.toast(
                "Imported {cards} cards, {profiles} profiles".format(**json.loads(s))))
        dlg.open(self, None, done)

    def _on_close(self, *_):
        self.preview.stop()
        return False
