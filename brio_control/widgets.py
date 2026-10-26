"""Small reusable rows: a debounced slider row and a toggle-button group row."""
from __future__ import annotations

import time

from gi.repository import Adw, GLib, Gtk

DEBOUNCE_MS = 50       # slider drags apply at most every ~50 ms
HOLD_S = 0.6           # ignore state echoes this long after a local change


class ScaleRow(Adw.PreferencesRow):
    """Title + value label on one line, a full-width slider below."""

    def __init__(self, title, lo, hi, step, fmt, on_commit, digits=0):
        super().__init__(title=title, activatable=False)
        self.fmt, self.on_commit, self.step = fmt, on_commit, step
        self._updating = False
        self._touched = 0.0
        self._pending = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=8, margin_bottom=6, margin_start=12, margin_end=12)
        head = Gtk.Box(spacing=6)
        self.title_label = Gtk.Label(label=title, xalign=0, hexpand=True)
        self.value_label = Gtk.Label(xalign=1, css_classes=["dim-label", "numeric"])
        head.append(self.title_label)
        head.append(self.value_label)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        self.scale.set_digits(digits)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.connect("value-changed", self._changed)
        box.append(head)
        box.append(self.scale)
        self.set_child(box)
        self._label(self.scale.get_value())

    def set_range(self, lo, hi):
        self._updating = True
        self.scale.set_range(lo, hi)
        self._updating = False

    def _snap(self, v):
        adj = self.scale.get_adjustment()
        lo = adj.get_lower()
        return lo + round((v - lo) / self.step) * self.step if self.step else v

    def _label(self, v):
        self.value_label.set_label(self.fmt(self._snap(v)))

    def _changed(self, scale):
        v = self._snap(scale.get_value())
        self._label(v)
        if self._updating:
            return
        self._touched = time.monotonic()
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(DEBOUNCE_MS, self._fire, v)

    def _fire(self, v):
        self._pending = None
        self.on_commit(v)
        return GLib.SOURCE_REMOVE

    def set_value(self, v):
        if time.monotonic() - self._touched < HOLD_S or self._pending:
            return
        self._updating = True
        self.scale.set_value(v)
        self._updating = False
        self._label(v)


class ChoiceRow(Adw.ActionRow):
    """Linked toggle buttons (e.g. FOV 90°/78°/65°, anti-flicker 50/60 Hz)."""

    def __init__(self, title, choices: list[tuple[object, str]], on_choose, subtitle=None):
        super().__init__(title=title)
        if subtitle:
            self.set_subtitle(subtitle)
        self.on_choose = on_choose
        self._updating = False
        box = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.buttons = {}
        first = None
        for value, label in choices:
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.connect("toggled", self._toggled, value)
            self.buttons[value] = b
            box.append(b)
        self.add_suffix(box)

    def _toggled(self, button, value):
        if button.get_active() and not self._updating:
            self.on_choose(value)

    def set_value(self, value):
        self._updating = True
        b = self.buttons.get(value)
        if b is not None:
            b.set_active(True)
        self._updating = False


def switch_row(title, on_toggle, subtitle=None) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row._updating = False

    def changed(r, _p):
        if not r._updating:
            on_toggle(r.get_active())
    row.connect("notify::active", changed)
    return row


def set_switch(row: Adw.SwitchRow, active: bool):
    row._updating = True
    row.set_active(bool(active))
    row._updating = False


def ask_text(parent, heading, body, initial, confirm, callback):
    """Adw.AlertDialog with an entry; callback(text) on confirm."""
    dlg = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial or "", activates_default=True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("ok", confirm)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def on_resp(d, resp):
        text = entry.get_text().strip()
        if resp == "ok" and text:
            callback(text)
    dlg.connect("response", on_resp)
    dlg.present(parent)
