"""Profile management dialog: names, per-app assignment, add/delete."""
from __future__ import annotations

from gi.repository import Adw, Gtk


class ProfilesDialog(Adw.PreferencesDialog):
    def __init__(self, win):
        super().__init__(title="Profiles", search_enabled=False)
        self.win = win
        self.client = win.client
        page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(
            title="Profiles",
            description="A profile is used while one of its apps has the camera open "
                        "(or is focused, if focus switching is on). Enter process names, "
                        "executable names or Flatpak app IDs, separated by commas.")
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], tooltip_text="Add profile")
        add.connect("clicked", self._add)
        self.group.set_header_suffix(add)
        page.add(self.group)
        self.add(page)
        self.rows = []
        self._build()
        self._sig = self.client.connect("state-changed", lambda *_: self._build())
        self.connect("closed", lambda *_: self.client.disconnect(self._sig))

    def _build(self):
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        st = self.client.state
        for p in st.get("profiles", []):
            exp = Adw.ExpanderRow(title=p["name"], subtitle=", ".join(p["apps"]) or "No apps")
            if p["id"] == st.get("activeProfile"):
                exp.add_suffix(Gtk.Label(label="Active", css_classes=["dim-label"]))
            name = Adw.EntryRow(title="Name", text=p["name"], show_apply_button=True)
            name.connect("apply", lambda r, pid=p["id"]: self.client.call(
                "RenameProfile", "(ss)", pid, r.get_text().strip() or "Profile"))
            exp.add_row(name)
            apps = Adw.EntryRow(title="Apps (e.g. zoom, obs, com.obsproject.Studio)",
                                text=", ".join(p["apps"]), show_apply_button=True)
            apps.connect("apply", lambda r, pid=p["id"]: self.client.call(
                "SetProfileApps", "(sas)", pid, [a.strip() for a in r.get_text().split(",")]))
            exp.add_row(apps)
            if p["id"] != "default":
                rm = Adw.ButtonRow(title="Delete Profile", css_classes=["destructive-action"])
                rm.connect("activated", lambda _r, pid=p["id"]: self.client.call("DeleteProfile", "(s)", pid))
                exp.add_row(rm)
            else:
                apps.set_sensitive(False)
                exp.set_subtitle("Used when no other profile matches")
            self.group.add(exp)
            self.rows.append(exp)

    def _add(self, _b):
        self.client.call("CreateProfile", "(s)", "New Profile")
