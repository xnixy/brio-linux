"""brio-control: GTK4 / libadwaita front end for briod."""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from .client import Client  # noqa: E402

APP_ID = "io.github.BrioControl"
VERSION = "0.1.0"

CSS = b"""
.preview { background: black; }
"""


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.client = None
        self.win = None
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_startup(self):
        Adw.Application.do_startup(self)
        from gi.repository import Gdk
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.win is None:
            from .window import Window
            self.client = Client()
            self.win = Window(self, self.client)
        self.win.present()
        shot = os.environ.get("BRIO_CONTROL_SCREENSHOT")   # development aid
        if shot:
            GLib.timeout_add(int(os.environ.get("BRIO_CONTROL_SCREENSHOT_DELAY", "4000")),
                             self._screenshot, shot)

    def _screenshot(self, spec):
        """BRIO_CONTROL_SCREENSHOT=out.png[:tab,tab…] renders the window per tab."""
        path, _, tabs = spec.partition(":")
        tabs = tabs.split(",") if tabs else [None]
        for i, tab in enumerate(tabs):
            GLib.timeout_add(700 * i, self._show_tab, tab)
            GLib.timeout_add(700 * i + 400, self._render, path if len(tabs) == 1 else
                             path.replace(".png", f"_{tab}.png"))
        return GLib.SOURCE_REMOVE

    def _show_tab(self, tab):
        if tab:
            self.win.stack.set_visible_child_name(tab)
        return GLib.SOURCE_REMOVE

    def _render(self, path):
        w, h = self.win.get_width(), self.win.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(self.win).snapshot(snap, w, h)
        tex = self.win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
        tex.save_to_png(path)
        return GLib.SOURCE_REMOVE

    def _about(self, *_):
        Adw.AboutDialog(application_name="BRIO Control", application_icon=APP_ID, version=VERSION,
                        comments="Logitech BRIO controls for Linux: HDR, field of view, 4K, profiles",
                        license_type=Gtk.License.GPL_3_0,
                        ).present(self.win)


def main():
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
