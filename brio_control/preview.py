"""Live preview: v4l2src → jpegdec → videoflip → gtk4paintablesink."""
from __future__ import annotations

import os
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gst, Gtk, Graphene  # noqa: E402

Gst.init(None)


class GridOverlay(Gtk.DrawingArea):
    """Rule-of-thirds lines over the displayed video rectangle."""

    def __init__(self, picture: Gtk.Picture):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.picture = picture
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, w, h):
        p = self.picture.get_paintable()
        ratio = p.get_intrinsic_aspect_ratio() if p else 0
        if not ratio:
            ratio = 16 / 9
        if w / h > ratio:
            vw, vh = h * ratio, h
        else:
            vw, vh = w, w / ratio
        x0, y0 = (w - vw) / 2, (h - vh) / 2
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_line_width(1)
        for i in (1, 2):
            x = round(x0 + vw * i / 3) + 0.5
            y = round(y0 + vh * i / 3) + 0.5
            cr.move_to(x, y0)
            cr.line_to(x, y0 + vh)
            cr.move_to(x0, y)
            cr.line_to(x0 + vw, y)
        cr.stroke()


class Preview(Gtk.Overlay):
    __gsignals__ = {
        "state": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),   # (running|stopped|error, message)
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("preview")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True)
        self.set_child(self.picture)
        self.grid = GridOverlay(self.picture)
        self.grid.set_visible(False)
        self.add_overlay(self.grid)
        self.placeholder = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                     css_classes=["dim-label", "title-3"])
        self.add_overlay(self.placeholder)
        self.pipeline = None
        self.sink = None
        self.flip = None
        self.devnode = None
        self.mode = None
        self.mirror = False
        self.running = False
        self.show_message("Preview off")

    # ---------------------------------------------------------------- public
    def configure(self, devnode, mode: dict | None, mirror: bool, grid: bool):
        restart = self.running and (devnode != self.devnode or mode != self.mode)
        self.devnode, self.mode = devnode, mode
        self.set_mirror(mirror)
        self.grid.set_visible(grid)
        if restart:
            self.stop()
            self.start()

    def set_mirror(self, mirror: bool):
        self.mirror = mirror
        if self.flip is not None:
            self.flip.set_property("video-direction", 4 if mirror else 0)  # horiz / identity

    def start(self):
        if self.running or not self.devnode or not self.mode:
            return
        m = self.mode
        if m["fourcc"] == "MJPG":
            caps = f"image/jpeg,width={m['width']},height={m['height']},framerate={m['fps']}/1"
            decode = "jpegdec"
        else:
            caps = f"video/x-raw,format=YUY2,width={m['width']},height={m['height']},framerate={m['fps']}/1"
            decode = "identity"
        desc = (f"v4l2src name=src device={self.devnode} io-mode=mmap ! {caps} ! {decode} ! queue max-size-buffers=2 leaky=downstream ! "
                f"videoflip name=flip ! gtk4paintablesink name=sink sync=false")
        try:
            self.pipeline = Gst.parse_launch(desc)
        except GLib.Error as e:
            self.emit("state", "error", e.message)
            return
        self.sink = self.pipeline.get_by_name("sink")
        self.flip = self.pipeline.get_by_name("flip")
        self.set_mirror(self.mirror)
        self.picture.set_paintable(self.sink.get_property("paintable"))
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        bus.connect("message::state-changed", self._on_state)
        self.running = True
        self.show_message("Starting preview…")
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # The error message on the bus carries the reason (e.g. EBUSY).
            pass
        self.emit("state", "running", "")

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.get_bus().remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = self.sink = self.flip = None
        self.picture.set_paintable(None)
        if self.running:
            self.running = False
            self.emit("state", "stopped", "")
        self.show_message("Preview off")

    def show_message(self, text: str | None):
        self.placeholder.set_label(text or "")
        self.placeholder.set_visible(bool(text))

    # ----------------------------------------------------------- snapshots
    def snapshot(self, directory: str, device_name: str) -> str:
        """Save the current frame (mirrored if mirroring is on) as PNG."""
        p = self.picture.get_paintable()
        if p is None or not self.running:
            raise RuntimeError("The preview is not running")
        img = p.get_current_image()
        texture = img if isinstance(img, Gdk.Texture) else self._render(img)
        if texture is None:
            raise RuntimeError("No frame available yet")
        if not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                raise RuntimeError(f"Cannot create {directory}: {e.strerror}") from None
        name = f"{device_name} {time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(directory, name)
        data = texture.save_to_png_bytes().get_data()
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            reason = {13: "Permission denied", 28: "Not enough disk space", 2: "Folder is missing"}.get(
                e.errno, e.strerror)
            raise RuntimeError(f"Could not save snapshot: {reason}") from None
        return path

    def _render(self, paintable):
        w, h = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        if not w or not h:
            return None
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        renderer = self.get_native().get_renderer() if self.get_native() else None
        if node is None or renderer is None:
            return None
        return renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))

    # -------------------------------------------------------------- bus
    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        text = err.message
        if dbg and ("busy" in dbg.lower() or "EBUSY" in dbg):
            text = "busy"
        self.stop()
        self.emit("state", "error", text)

    def _on_state(self, _bus, msg):
        if msg.src is self.pipeline:
            _old, new, _pending = msg.parse_state_changed()
            if new == Gst.State.PLAYING:
                self.show_message(None)
